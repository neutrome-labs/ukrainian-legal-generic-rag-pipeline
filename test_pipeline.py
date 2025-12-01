#!/usr/bin/env python3
"""
Quick test script for the pipeline.
Tests each component individually.
"""

import logging
import json
import sys
from pathlib import Path

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_config():
    """Test configuration loading"""
    print("\n" + "=" * 60)
    print("Testing Configuration")
    print("=" * 60)
    
    from src.config import load_config, CONSTITUTION_NREG
    
    config = load_config()
    print(f"✓ Config loaded")
    print(f"  - Rada API base: {config.rada.base_url}")
    print(f"  - R2 configured: {config.r2.validate()}")
    print(f"  - Constitution NREG: {CONSTITUTION_NREG}")
    print(f"  - Output dir: {config.output_dir}")
    
    return True


def test_api_client():
    """Test Rada API client"""
    print("\n" + "=" * 60)
    print("Testing Rada API Client")
    print("=" * 60)
    
    from src.rada_api_client import RadaAPIClient
    
    client = RadaAPIClient()
    
    # Test getting primary acts list
    print("Fetching primary acts list...")
    nregs = client.get_primary_acts_list()
    print(f"✓ Found {len(nregs)} primary acts")
    
    if nregs:
        print(f"  - First 5: {nregs[:5]}")
    
    # Test getting constitution
    print("\nFetching Constitution card...")
    from src.config import CONSTITUTION_NREG
    card = client.get_document_card(CONSTITUTION_NREG)
    
    if card:
        print(f"✓ Constitution card fetched")
        print(f"  - Title: {card.get('nazva', 'N/A')}")
    else:
        print("✗ Could not fetch Constitution card")
        return False
    
    # Test getting text
    print("\nFetching Constitution text...")
    text = client.get_document_text(CONSTITUTION_NREG)
    
    if text:
        print(f"✓ Text fetched ({len(text)} characters)")
        print(f"  - Preview: {text[:200]}...")
    else:
        print("✗ Could not fetch Constitution text")
        return False
    
    return True


def test_markdown_converter():
    """Test markdown converter"""
    print("\n" + "=" * 60)
    print("Testing Markdown Converter")
    print("=" * 60)
    
    from src.markdown_converter import MarkdownConverter
    
    converter = MarkdownConverter()
    
    # Sample legal text
    sample = """
    Розділ I
    ЗАГАЛЬНІ ЗАСАДИ
    
    Стаття 1. Україна є суверенна і незалежна, демократична, соціальна, правова держава.
    
    Стаття 2. Суверенітет України поширюється на всю її територію.
    Україна є унітарною державою.
    
    Стаття 3. Людина, її життя і здоров'я, честь і гідність визнаються найвищою соціальною цінністю.
    """
    
    chunks = converter.split_into_chunks(
        sample,
        doc_id="test-doc",
        title="Тестовий документ"
    )
    
    print(f"✓ Created {len(chunks)} chunks")
    for chunk in chunks:
        print(f"  - {chunk.chunk_id}: {chunk.section_type} - {chunk.section_number or 'N/A'}")
    
    return True


def test_local_uploader():
    """Test local storage uploader"""
    print("\n" + "=" * 60)
    print("Testing Local Storage Uploader")
    print("=" * 60)
    
    from src.r2_uploader import LocalStorage
    from src.markdown_converter import DocumentChunk
    
    # Create test output directory
    output_dir = Path("./test_output")
    uploader = LocalStorage(str(output_dir))
    
    # Create test chunk
    chunk = DocumentChunk(
        doc_id="test-doc",
        chunk_id="test-doc_0001",
        chunk_index=1,
        content="# Тестова стаття\n\nЦе тестовий контент українською мовою.",
        title="Тестовий документ",
        section_type="article",
        section_number="Стаття 1",
        section_title="Загальні положення",
        metadata={"test": True}
    )
    
    result = uploader.upload_chunk(chunk, "laws")
    
    if result.success:
        print(f"✓ Chunk saved to: {output_dir / result.key}")
        
        # Verify file exists
        file_path = output_dir / result.key
        if file_path.exists():
            content = file_path.read_text(encoding='utf-8')
            print(f"  - File size: {len(content)} bytes")
            print(f"  - Has frontmatter: {'---' in content}")
        
        return True
    else:
        print(f"✗ Upload failed: {result.error}")
        return False


def test_constitution_processing():
    """Test full Constitution processing"""
    print("\n" + "=" * 60)
    print("Testing Constitution Processing")
    print("=" * 60)
    
    from src.rada_api_client import RadaAPIClient
    from src.markdown_converter import ConstitutionProcessor
    from src.config import CONSTITUTION_NREG
    
    client = RadaAPIClient()
    processor = ConstitutionProcessor()
    
    # Get Constitution text
    print("Fetching Constitution...")
    text = client.get_document_text(CONSTITUTION_NREG)
    
    if not text:
        print("✗ Could not fetch Constitution text")
        return False
    
    print(f"✓ Fetched {len(text)} characters")
    
    # Process into chunks
    print("Processing into chunks...")
    chunks = processor.process(text)
    
    print(f"✓ Created {len(chunks)} chunks")
    
    # Show sample
    if chunks:
        sample = chunks[min(5, len(chunks)-1)]
        print(f"\nSample chunk (index {sample.chunk_index}):")
        print(f"  - ID: {sample.chunk_id}")
        print(f"  - Section: {sample.section_number}")
        print(f"  - Content preview: {sample.content[:100]}...")
    
    return True


def run_all_tests():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("UKRAINIAN LEGAL RAG PIPELINE - TEST SUITE")
    print("=" * 60)
    
    tests = [
        ("Configuration", test_config),
        ("API Client", test_api_client),
        ("Markdown Converter", test_markdown_converter),
        ("Local Uploader", test_local_uploader),
        ("Constitution Processing", test_constitution_processing),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            success = test_func()
            results.append((name, success))
        except Exception as e:
            logger.error(f"Test '{name}' failed with error: {e}", exc_info=True)
            results.append((name, False))
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    passed = 0
    for name, success in results:
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"  {status}: {name}")
        if success:
            passed += 1
    
    print(f"\nTotal: {passed}/{len(results)} tests passed")
    
    return passed == len(results)


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
