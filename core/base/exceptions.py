"""
p2workflowy 黄金の再構築: パイプライン専用例外クラス
各フェーズにおける異常系を厳格に捕捉するための定義。
"""

class P2WorkflowyError(Exception):
    """プロジェクト全体のベース例外"""
    def __init__(self, message: str, phase: int | None = None):
        self.phase = phase
        formatted_msg = f"[Phase {phase}] {message}" if phase else message
        super().__init__(formatted_msg)


class PreprocessorError(P2WorkflowyError):
    """Phase 1: テキスト整形に関わるエラー"""
    def __init__(self, message: str):
        super().__init__(message, phase=1)


class MetaExtractionError(P2WorkflowyError):
    """Phase 2: メタデータ/DNA 抽出に関わるエラー"""
    def __init__(self, message: str):
        super().__init__(message, phase=2)


class StructureError(P2WorkflowyError):
    """Phase 3: 構造化/見出し判定に関わるエラー"""
    def __init__(self, message: str):
        super().__init__(message, phase=3)


class SlicingError(StructureError):
    """書籍モード等のスライス失敗"""
    pass


class TranslationError(P2WorkflowyError):
    """Phase 4: 翻訳処理に関わるエラー"""
    def __init__(self, message: str):
        super().__init__(message, phase=4)


class ExportError(P2WorkflowyError):
    """Phase 5: ファイル出力/フォーマットに関わるエラー"""
    def __init__(self, message: str):
        super().__init__(message, phase=5)


class LLMClientError(P2WorkflowyError):
    """API 基盤に関わるエラー"""
    pass
