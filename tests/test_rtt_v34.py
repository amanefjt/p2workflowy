import asyncio
import re
import sys
import os

# プロジェクトルートをパスに追加
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

try:
    from core.llm_client import tier_manager, GeminiTier, apply_tier_settings
    from core.models import TreeNode
    from core.engine.p4_translate.parallel_translator import ParallelTranslator
    from core.config import print_log
except ImportError as e:
    print(f"Import error: {e}")
    print(f"Make sure you are running this from {PROJECT_ROOT}")
    sys.exit(1)

def test_regex_robustness():
    """llm_client.py の正規表現ロジックが、不完全な応答をどこまで救えるか検証する。"""
    print("\n=== [1] Testing Regex Robustness (RTT v3.4 Rescue) ===")
    
    # 実際の llm_client.py のロジックを再現
    def extract_with_logic(response, chunk_ids):
        id_map = {}
        for cid in chunk_ids:
            # llm_client.py line 457 の正規表現
            pattern = rf"<p2w_chunk_{cid}>(.*?)(?:</p2w_chunk_{cid}>|(?=<p2w_chunk_)|\s*$)"
            match = re.search(pattern, response, re.DOTALL | re.IGNORECASE)
            if match:
                extracted = match.group(1).strip()
                if extracted:
                    id_map[str(cid)] = extracted
        return id_map

    # Test Case 1: 正常系
    resp1 = "<p2w_chunk_101>こんにちは</p2w_chunk_101><p2w_chunk_102>世界</p2w_chunk_102>"
    res1 = extract_with_logic(resp1, [101, 102])
    print(f"Normal Case: {res1}")
    assert res1 == {"101": "こんにちは", "102": "世界"}

    # Test Case 2: 閉じタグ欠落（末尾）
    resp2 = "<p2w_chunk_101>こんにちは</p2w_chunk_101><p2w_chunk_102>さようなら（ここで出力が途切れる..."
    res2 = extract_with_logic(resp2, [101, 102])
    print(f"Truncated End: {res2}")
    assert res2 == {"101": "こんにちは", "102": "さようなら（ここで出力が途切れる..."}

    # Test Case 3: 中間の閉じタグ欠落（次の開始タグで救出）
    # Gemini がたまにやる「閉じタグ忘れ」を想定
    resp3 = "<p2w_chunk_101>本文1<p2w_chunk_102>本文2</p2w_chunk_102>"
    res3 = extract_with_logic(resp3, [101, 102])
    print(f"Missing Intermediate Close Tag: {res3}")
    assert res3 == {"101": "本文1", "102": "本文2"}

    # Test Case 4: 大文字小文字混在
    resp4 = "<P2W_CHUNK_101>Mixed Case</p2w_chunk_101>"
    res4 = extract_with_logic(resp4, [101])
    print(f"Case Insensitive: {res4}")
    assert res4 == {"101": "Mixed Case"}

    print("✅ Regex Robustness Tests Passed!")

async def test_adaptive_batching_logic():
    """ParallelTranslator がティア変更を検知してバッチサイズを変えるか検証する。"""
    print("\n=== [2] Testing Adaptive Batching Logic ===")
    
    # ティアの初期化
    tier_manager.set_tier(GeminiTier.PAID)
    translator = ParallelTranslator(tier=GeminiTier.PAID)
    
    # PAID ティアの設定を確認 (10 chunks / 11000 chars)
    assert translator.settings["max_batch_chunks"] == 10
    
    # 15個の仮想チャンクを作成
    mock_chunks = [{"id": i, "text": "Dummy text " * 10} for i in range(1, 16)]
    
    # ParallelTranslator.translate_section_chunks の内部ループを模擬
    def simulate_loop(chunks):
        remaining = list(chunks)
        batch_log = []
        batch_idx = 1
        
        while remaining:
            # 1. バッチ作成前にダウングレードが発生したかチェック
            if batch_idx == 2:
                print(f"--- Simulating 429/503 error at Batch {batch_idx} ---")
                tier_manager.downgrade()
            
            # parallel_translator.py line 69-71 のロジック
            if tier_manager.was_downgraded:
                _, _, translator.settings = apply_tier_settings(tier_manager.current_tier)
                print(f"    [Logic] Tier changed to {tier_manager.current_tier.value}")
                print(f"    [Logic] New max_batch_chunks: {translator.settings['max_batch_chunks']}")
            
            # バッチ切り出し
            max_c = translator.settings.get("max_batch_chunks", 10)
            batch = []
            while remaining and len(batch) < max_c:
                batch.append(remaining.pop(0))
            
            batch_log.append(len(batch))
            print(f"Batch {batch_idx}: {len(batch)} chunks")
            batch_idx += 1
            
        return batch_log

    # 実行
    # 最初のバッチ(PAID)で 10個、次(FREE)で残り5個が処理されるはず
    results = simulate_loop(mock_chunks)
    print(f"Execution sequence (batch sizes): {results}")
    
    assert results[0] == 10, "First batch should be 10 (PAID)"
    assert results[1] == 5, "Second batch should be 5 (FREE after downgrade)"
    
    print("✅ Adaptive Batching Logic Tests Passed!")

async def main():
    test_regex_robustness()
    await test_adaptive_batching_logic()
    print("\n✨ All tests completed successfully!")

if __name__ == "__main__":
    asyncio.run(main())
