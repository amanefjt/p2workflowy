from src.skills import PaperProcessorSkills
import unittest
from unittest.mock import MagicMock, patch

class TestSplitLogic(unittest.TestCase):
    @patch('src.skills.LLMProcessor')
    def setUp(self, mock_llm):
        # LLMをモック化して初期化エラーを回避
        self.skills = PaperProcessorSkills()

    def test_ordered_splitting_with_running_heads(self):
        print("\n--- Testing Ordered Splitting with Running Heads ---")
        
        # タイトルが本文中にランニングヘッドとして何度も現れる状況をシミュレート
        raw_text = """
        Page 1: An Associational Ethos
        Introduction
        This is the actual start.
        Page 2: An Associational Ethos
        Section 1: Details
        More details here.
        Section 2: Results
        Page 3: An Associational Ethos
        Final results.
        """
        
        # 要約から抽出された（はずの）節タイトル
        section_titles = [
            "Introduction",
            "Section 1: Details",
            "Section 2: Results"
        ]
        
        # An Associational Ethos は節タイトルではない（ランニングヘッド）
        # もし昔のロジックだと、間違って分割されたり混乱したりする可能性がある
        
        chunks = self.skills._split_text_by_sections(raw_text, section_titles)
        
        print(f"Chunks returned: {len(chunks)}")
        for i, c in enumerate(chunks):
            print(f"Chunk {i}: {c[:50]}...")

        # 期待値:
        # 0: Page 1: An Associational Ethos (Introductionの前の部分)
        # 1: Introduction ... Page 2: An Associational Ethos
        # 2: Section 1: Details ...
        # 3: Section 2: Results ...
        
        self.assertTrue(any("Introduction" in chunks[1] for _ in [1]))
        self.assertTrue(any("Section 1: Details" in c for c in chunks))
        self.assertTrue(any("Section 2: Results" in c for c in chunks))

    def test_title_not_found_fallback(self):
        print("\n--- Testing Fallback when title not found ---")
        raw_text = "Some random text that does not contain the titles."
        section_titles = ["Missing Title 1", "Missing Title 2"]
        
        # フォールバックして文字数分割されるはず
        chunks = self.skills._split_text_by_sections(raw_text, section_titles)
        self.assertGreater(len(chunks), 0)
        print("✓ Fallback to length-based splitting worked.")

if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(TestSplitLogic)
    unittest.TextTestRunner().run(suite)
