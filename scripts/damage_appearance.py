"""从无框裁剪图提取特征，为跨行程病害匹配计算外观分数。"""
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class CropFeatures:
    points: np.ndarray
    descriptors: np.ndarray
    image_area: int


def _coverage(points, image_area):
    return cv2.contourArea(cv2.convexHull(points)) / image_area


def describe_crop(path: str | Path | None) -> CropFeatures | None:
    """读取 events.csv 的 crop 路径，返回 ORB 特征点、描述符和图像面积；证据不足时返回 None。"""
    if not path or not Path(path).is_file():
        return None
    # 先读取文件字节再解码，兼容包含中文的 Windows 路径。
    encoded = np.fromfile(path, dtype=np.uint8)
    if not encoded.size:
        return None
    gray = cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE)
    if gray is None or min(gray.shape) < 20:
        return None
    scale = min(1.5, 400 / max(gray.shape))
    resized = cv2.resize(gray, None, fx=scale, fy=scale,
                         interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    # nfeatures 是特征点数上限；调高会增加匹配开销。fastThreshold 调低可在低对比度路面提取更多点。
    orb = cv2.ORB_create(nfeatures=650, edgeThreshold=12, fastThreshold=12)
    keypoints, descriptors = orb.detectAndCompute(resized, None)
    if descriptors is None or len(keypoints) < 24:
        return None
    points = np.float32([point.pt for point in keypoints])
    if _coverage(points, resized.size) < 0.08:
        return None
    return CropFeatures(points, descriptors, resized.size)


def _distinct_matches(matcher, left, right):
    # 如果最近的两个候选几乎一样像，这个局部纹理不足以区分相邻病害。
    return {first.queryIdx: first.trainIdx
            for first, second in matcher.knnMatch(left, right, k=2)
            if first.distance <= 48 and first.distance < 0.78 * second.distance}


def compare_crops(left: CropFeatures | None, right: CropFeatures | None) -> float | None:
    """比较两组特征，通过双向匹配和 RANSAC 几何校验返回 0 到 1 的外观支持分。

    纹理或几何证据不足时可返回 None。调用方把分数与阈值、位置和候选数量一起用于归档。
    """
    if left is None or right is None:
        return None
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    forward = _distinct_matches(matcher, left.descriptors, right.descriptors)
    reverse = _distinct_matches(matcher, right.descriptors, left.descriptors)
    pairs = [(a, b) for a, b in forward.items() if reverse.get(b) == a]
    if len(pairs) < 6:
        return 0.0
    a, b = np.asarray(pairs).T
    src, dst = left.points[a], right.points[b]
    # 两趟拍摄允许尺度和视角变化；通过几何检验的点还要分布在足够大的病害区域。
    # 3.0 是缩放后裁剪图上的像素容差；越小，对两图特征点几何对齐的要求越严格。
    transform, mask = cv2.estimateAffine2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0)
    if transform is None or mask is None:
        return None
    inliers = mask.ravel().astype(bool)
    count = int(inliers.sum())
    if count < 6:
        return 0.0
    linear = transform[:, :2]
    scales = np.linalg.svd(linear, compute_uv=False)
    if np.linalg.det(linear) <= 0 or scales[1] < 0.4 or scales[0] > 2.5 or scales[0] / scales[1] > 1.8:
        return None
    coverage = min(_coverage(src[inliers], left.image_area), _coverage(dst[inliers], right.image_area))
    if coverage < 0.08:
        return None
    # 小样本、相互矛盾的匹配或过于集中的匹配，任何一种都限制最终分数。
    return min(1.0, count / 24, count / len(pairs), coverage / 0.30)
