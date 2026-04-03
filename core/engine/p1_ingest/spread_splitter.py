import numpy as np
from PIL import Image
from typing import Tuple, List

class SpreadSplitter:
    """
    見開き（Spread）PDF ページを個別のページに分割するユーティリティ。
    英語書籍を想定し、左から右（LtoR）の順序で分割を行う。
    """
    
    @staticmethod
    def find_gutter_x(img: Image.Image) -> Tuple[int, bool]:
        """
        画像の輝度分布から中央の綴じ目（ノド）の X 座標を検出する。
        幅の 40% ～ 60% の範囲で、最も白い（インクが少ない）列を探索する。
        """
        img_gray = img.convert("L")
        img_array = np.array(img_gray)
        height, width = img_array.shape
        
        # 探索範囲: 中央 20%
        x_min = int(width * 0.40)
        x_max = int(width * 0.60)
        
        if x_max <= x_min:
            return width // 2, False
            
        # 列ごとの輝度合計（白=高, 黒=低）
        col_sums = np.sum(img_array[:, x_min:x_max], axis=0).astype(float)
        
        # 移動平均でノイズ除去（平滑化）
        window = 20
        if len(col_sums) > window:
            smoothed = np.convolve(col_sums, np.ones(window)/window, mode='valid')
            relative_idx = np.argmax(smoothed)
            max_val = smoothed.max()
            split_x = x_min + int(relative_idx) + window // 2
        else:
            relative_idx = np.argmax(col_sums)
            max_val = col_sums.max()
            split_x = x_min + int(relative_idx)
            
        # 信頼性判定: 最も白い列が平均より十分に白いか (1.1倍以上)
        avg_val = col_sums.mean()
        is_reliable = (max_val > avg_val * 1.1) and (avg_val < 250 * height)
        
        return split_x, is_reliable

    @staticmethod
    def split_spread_ltr(img: Image.Image) -> List[Image.Image]:
        """
        見開き画像を左ページ・右ページの順序で分割する (LtoR)。
        """
        width, height = img.size
        split_x, is_reliable = SpreadSplitter.find_gutter_x(img)
        
        # 信頼性が低くても中央で切る（アスペクト比で見開き判定されている前提）
        if not is_reliable:
            split_x = width // 2
            
        img_left = img.crop((0, 0, split_x, height))
        img_right = img.crop((split_x, 0, width, height))
        
        return [img_left, img_right]

    @staticmethod
    def is_spread(img: Image.Image) -> bool:
        """
        画像が横長（見開き）であるかを判定する。
        """
        width, height = img.size
        # アスペクト比 1.1 以上を見開き候補とする
        return (width / height) > 1.1
