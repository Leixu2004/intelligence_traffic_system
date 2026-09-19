import cv2
import numpy as np

def order_points(pts):
    """
    对输入的 4 个顶点按 [左上 tl, 右上 tr, 右下 br, 左下 bl] 进行排序
    """
    rect = np.zeros((4, 2), dtype="float32")
    pts = np.array(pts, dtype="float32")
    
    # 左上角的 x+y 和最小，右下角的 x+y 和最大
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    
    # 右上角的 y-x 差最小（或 x-y 差最大），左下角的 y-x 差最大
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    
    return rect

def warp_plate(img, pts):
    """
    4 点透视变换校正倾斜车牌
    :param img: 输入图像
    :param pts: 4 个顶点坐标 (4x2)
    :return: 经过透视校正后的水平正向车牌图像
    """
    rect = order_points(pts)
    (tl, tr, br, bl) = rect
    
    # 计算变换后的宽度和高度
    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    width = max(int(width_a), int(width_b))
    
    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)
    height = max(int(height_a), int(height_b))
    
    if width <= 0 or height <= 0:
        return img
        
    # 目标映射平面
    dst = np.array([
        [0, 0],
        [width - 1, 0],
        [width - 1, height - 1],
        [0, height - 1]
    ], dtype="float32")
    
    # 单应性矩阵计算与透视变换
    M = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(img, M, (width, height))
    return warped

def auto_rectify_plate(plate_roi):
    """
    自适应车牌校正：提取车牌内轮廓四边形进行透视变换 (遵循 LPR-CORNER-SPEC-v1.0 规范)
    """
    if plate_roi is None or plate_roi.size == 0:
        return plate_roi
        
    h, w = plate_roi.shape[:2]
    # 如果图片太小，直接返回
    if h < 10 or w < 20:
        return plate_roi

    # 1. 灰度与高斯平滑
    gray = cv2.cvtColor(plate_roi, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    
    # 2. Canny 边缘检测
    edged = cv2.Canny(blurred, 50, 200)
    
    # 3. 形态学闭运算 (连接断裂的边缘)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    closed = cv2.morphologyEx(edged, cv2.MORPH_CLOSE, kernel, iterations=2)
    
    # 4. 轮廓提取
    contours, _ = cv2.findContours(closed.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    # 按面积降序取前 5 个
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:5]
    
    for c in contours:
        peri = cv2.arcLength(c, True)
        # 5. 多边形逼近 (精度设为 3%)
        approx = cv2.approxPolyDP(c, 0.03 * peri, True)
        
        # 6. 启发式过滤: 必须是 4 个顶点，必须是凸多边形，面积大于 ROI 的 30%
        if len(approx) == 4 and cv2.isContourConvex(approx):
            area = cv2.contourArea(approx)
            if area > 0.3 * (w * h):
                pts = approx.reshape(4, 2)
                return warp_plate(plate_roi, pts)
                
    # 7. Fallback: 找不到有效四边形时，默认使用原 ROI 进行处理（相当于不进行斜切变换）
    default_pts = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype="float32")
    return warp_plate(plate_roi, default_pts)
