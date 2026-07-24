/*
 * Custom spatial NMS (center-bin + 3x3 IoU), same semantics as
 * openpvscope.detection.template_match.optimized_spatial_nms (Python).
 *
 * Suppress when iou > threshold (not >=).
 * Returns original input indices (after filtering zero-area boxes).
 */

#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>
#include <pybind11/functional.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace py = pybind11;

namespace {

inline std::int64_t pack_bin(int bx, int by) {
    return (static_cast<std::int64_t>(static_cast<std::uint32_t>(bx)) << 32) |
           static_cast<std::uint32_t>(by);
}

struct PackedHash {
    std::size_t operator()(std::int64_t k) const noexcept {
        // splitmix64-ish
        std::uint64_t x = static_cast<std::uint64_t>(k);
        x += 0x9e3779b97f4a7c15ULL;
        x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
        x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
        return static_cast<std::size_t>(x ^ (x >> 31));
    }
};

using BinMap = std::unordered_map<std::int64_t, std::vector<int>, PackedHash>;

py::list spatial_nms_impl(
    py::array_t<float, py::array::c_style | py::array::forcecast> boxes_xyxy,
    py::array_t<float, py::array::c_style | py::array::forcecast> scores,
    float iou_threshold,
    py::object progress_callback) {
    auto boxes_buf = boxes_xyxy.request();
    auto scores_buf = scores.request();

    if (boxes_buf.ndim != 2 || boxes_buf.shape[1] != 4) {
        throw std::runtime_error("boxes_xyxy must be (N, 4) float32");
    }
    if (scores_buf.ndim != 1 || scores_buf.shape[0] != boxes_buf.shape[0]) {
        throw std::runtime_error("scores must be (N,) matching boxes");
    }

    const std::size_t n_in = static_cast<std::size_t>(boxes_buf.shape[0]);
    if (n_in == 0) {
        return py::list();
    }

    const float* bin_boxes = static_cast<const float*>(boxes_buf.ptr);
    const float* bin_scores = static_cast<const float*>(scores_buf.ptr);

    std::vector<int> valid;
    valid.reserve(n_in);
    std::vector<float> boxes;  // packed x1,y1,x2,y2 for valid
    boxes.reserve(n_in * 4);
    std::vector<float> sc;
    sc.reserve(n_in);
    std::vector<float> area;
    area.reserve(n_in);

    for (std::size_t i = 0; i < n_in; ++i) {
        float x1 = bin_boxes[i * 4 + 0];
        float y1 = bin_boxes[i * 4 + 1];
        float x2 = bin_boxes[i * 4 + 2];
        float y2 = bin_boxes[i * 4 + 3];
        if (x2 < x1) std::swap(x1, x2);
        if (y2 < y1) std::swap(y1, y2);
        const float a = (x2 - x1) * (y2 - y1);
        if (a <= 0.0f) continue;
        valid.push_back(static_cast<int>(i));
        boxes.push_back(x1);
        boxes.push_back(y1);
        boxes.push_back(x2);
        boxes.push_back(y2);
        sc.push_back(bin_scores[i]);
        area.push_back(a);
    }

    const int n = static_cast<int>(valid.size());
    if (n == 0) {
        return py::list();
    }

    auto report = [&](const std::string& msg) {
        if (!progress_callback.is_none()) {
            py::gil_scoped_acquire acquire;
            progress_callback(msg);
        }
    };

    // Heavy loops without holding the GIL (re-acquire only for progress)
    py::gil_scoped_release release;

    // Sort by score descending (stable like mergesort: use stable_sort)
    std::vector<int> order(n);
    for (int i = 0; i < n; ++i) order[i] = i;
    std::stable_sort(order.begin(), order.end(), [&](int a, int b) {
        if (sc[a] != sc[b]) return sc[a] > sc[b];
        return a < b;
    });

    double sum_w = 0.0, sum_h = 0.0;
    float min_x = boxes[0], min_y = boxes[1];
    for (int i = 0; i < n; ++i) {
        const float x1 = boxes[i * 4 + 0];
        const float y1 = boxes[i * 4 + 1];
        const float x2 = boxes[i * 4 + 2];
        const float y2 = boxes[i * 4 + 3];
        sum_w += (x2 - x1);
        sum_h += (y2 - y1);
        if (x1 < min_x) min_x = x1;
        if (y1 < min_y) min_y = y1;
    }
    const float avg_w = static_cast<float>(sum_w / n);
    const float avg_h = static_cast<float>(sum_h / n);
    const float bin_size = std::max(std::max(avg_w, avg_h), 1.0f) * 2.0f;

    std::vector<int> bin_x(n), bin_y(n);
    for (int i = 0; i < n; ++i) {
        const float cx = 0.5f * (boxes[i * 4 + 0] + boxes[i * 4 + 2]);
        const float cy = 0.5f * (boxes[i * 4 + 1] + boxes[i * 4 + 3]);
        bin_x[i] = static_cast<int>(std::floor((cx - min_x) / bin_size));
        bin_y[i] = static_cast<int>(std::floor((cy - min_y) / bin_size));
    }

    BinMap spatial_bins;
    spatial_bins.reserve(static_cast<std::size_t>(n / 2) + 16);
    const int prog_step = std::max(50'000, n / 20);

    for (int i = 0; i < n; ++i) {
        if (i > 0 && (i % prog_step == 0 || i + 1 == n)) {
            report("NMS binning " + std::to_string(i) + "/" + std::to_string(n) + " [C++]");
        }
        spatial_bins[pack_bin(bin_x[i], bin_y[i])].push_back(i);
    }

    std::vector<char> suppressed(static_cast<std::size_t>(n), 0);
    std::vector<int> keep;
    keep.reserve(static_cast<std::size_t>(std::max(16, n / 100)));

    for (int si = 0; si < n; ++si) {
        if (si > 0 && (si % prog_step == 0 || si + 1 == n)) {
            report("NMS suppress " + std::to_string(si) + "/" + std::to_string(n) +
                   " (kept " + std::to_string(keep.size()) + ") [C++]");
        }
        const int local_idx = order[si];
        if (suppressed[static_cast<std::size_t>(local_idx)]) continue;

        keep.push_back(valid[static_cast<std::size_t>(local_idx)]);

        const float c0 = boxes[local_idx * 4 + 0];
        const float c1 = boxes[local_idx * 4 + 1];
        const float c2 = boxes[local_idx * 4 + 2];
        const float c3 = boxes[local_idx * 4 + 3];
        const float current_area = area[static_cast<std::size_t>(local_idx)];
        const int bx0 = bin_x[static_cast<std::size_t>(local_idx)];
        const int by0 = bin_y[static_cast<std::size_t>(local_idx)];

        for (int dx = -1; dx <= 1; ++dx) {
            for (int dy = -1; dy <= 1; ++dy) {
                const auto it = spatial_bins.find(pack_bin(bx0 + dx, by0 + dy));
                if (it == spatial_bins.end()) continue;
                for (int other_idx : it->second) {
                    if (other_idx == local_idx) continue;
                    if (suppressed[static_cast<std::size_t>(other_idx)]) continue;

                    const float o0 = boxes[other_idx * 4 + 0];
                    const float o1 = boxes[other_idx * 4 + 1];
                    const float o2 = boxes[other_idx * 4 + 2];
                    const float o3 = boxes[other_idx * 4 + 3];

                    const float xx1 = std::max(c0, o0);
                    const float yy1 = std::max(c1, o1);
                    const float xx2 = std::min(c2, o2);
                    const float yy2 = std::min(c3, o3);
                    if (xx2 > xx1 && yy2 > yy1) {
                        const float inter = (xx2 - xx1) * (yy2 - yy1);
                        const float uni =
                            current_area + area[static_cast<std::size_t>(other_idx)] - inter;
                        const float iou = uni > 0.0f ? inter / uni : 0.0f;
                        if (iou > iou_threshold) {
                            suppressed[static_cast<std::size_t>(other_idx)] = 1;
                        }
                    }
                }
            }
        }
    }

    py::gil_scoped_acquire acquire;
    py::list out;
    for (int idx : keep) {
        out.append(idx);
    }
    return out;
}

}  // namespace

PYBIND11_MODULE(_spatial_nms, m) {
    m.doc() = "Custom center-bin spatial NMS (C++)";
    m.def(
        "spatial_nms",
        &spatial_nms_impl,
        py::arg("boxes_xyxy"),
        py::arg("scores"),
        py::arg("iou_threshold"),
        py::arg("progress_callback") = py::none(),
        "Center-bin spatial NMS; suppress when iou > threshold. "
        "Returns list of original input indices to keep.");
}
