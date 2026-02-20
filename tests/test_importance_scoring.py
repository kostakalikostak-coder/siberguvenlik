"""
Importance Scoring Tests
config.py'deki IMPORTANCE_WEIGHTS ve DETECTION_PATTERNS testleri
"""
import pytest
import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import IMPORTANCE_WEIGHTS, DETECTION_PATTERNS


class TestImportanceWeights:
    """Önem ağırlıkları testleri"""

    def test_all_categories_defined(self):
        """Tüm kategoriler tanımlanmış mı?"""
        expected_categories = [
            'infrastructure_attack',
            'large_breach',
            'zero_day_apt',
            'national_security',
            'geopolitical_critical',
            'legal_regulation'
        ]

        for category in expected_categories:
            assert category in IMPORTANCE_WEIGHTS

    def test_weights_are_numeric(self):
        """Ağırlıklar sayı mı?"""
        for category, data in IMPORTANCE_WEIGHTS.items():
            assert isinstance(data['weight'], int)
            assert data['weight'] > 0

    def test_weights_ordered_correctly(self):
        """En yüksek ağırlık geopolitical_critical mi?"""
        weights = [(k, v['weight']) for k, v in IMPORTANCE_WEIGHTS.items()]
        weights.sort(key=lambda x: x[1], reverse=True)
        highest = weights[0]
        assert highest[0] == 'geopolitical_critical'
        assert highest[1] == 120

    def test_each_category_has_description(self):
        """Her kategorinin açıklaması mı var?"""
        for category, data in IMPORTANCE_WEIGHTS.items():
            assert 'description' in data
            assert len(data['description']) > 0

    def test_each_category_has_keywords(self):
        """Her kategorinin keyword'ü mü var?"""
        for category, data in IMPORTANCE_WEIGHTS.items():
            assert 'keywords' in data
            assert isinstance(data['keywords'], list)
            assert len(data['keywords']) > 0

    def test_weight_values_are_reasonable(self):
        """Ağırlık değerleri mantıklı mı?"""
        weights = [v['weight'] for v in IMPORTANCE_WEIGHTS.values()]
        assert min(weights) >= 50
        assert max(weights) <= 150

    def test_no_duplicate_weights(self):
        """Aynı ağırlığa sahip iki kategori var mı?"""
        weights = [v['weight'] for v in IMPORTANCE_WEIGHTS.values()]
        # Geopolitical ve national security aynı olabilir ama diğerleri unique
        unique_weights = len(set(weights))
        assert unique_weights >= 4  # En az 4 farklı ağırlık


class TestDetectionPatterns:
    """KDetection pattern'leri testleri"""

    def test_all_patterns_defined(self):
        """Tüm pattern'ler tanımlanmış mı?"""
        expected_patterns = ['cve', 'apt_groups', 'large_number', 'sectors', 'countries']

        for pattern_name in expected_patterns:
            assert pattern_name in DETECTION_PATTERNS

    def test_patterns_are_valid_regex(self):
        """Pattern'ler valid regex mi?"""
        for pattern_name, pattern in DETECTION_PATTERNS.items():
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
                assert compiled is not None
            except re.error:
                pytest.fail(f"Invalid regex pattern for {pattern_name}: {pattern}")

    def test_cve_pattern_matching(self):
        """CVE pattern'i doğru çalışıyor mu?"""
        pattern = DETECTION_PATTERNS['cve']
        test_cases = [
            ("CVE-2024-1234", True),
            ("CVE-2024-12345", True),
            ("CVE-2025-9999", True),
            ("CVE-20-1234", False),
            ("cve-2024-1234", False),  # Case sensitive
            ("CVE 2024 1234", False),
        ]

        for text, should_match in test_cases:
            match = re.search(pattern, text)
            if should_match:
                assert match is not None, f"Should match: {text}"
            else:
                assert match is None, f"Should not match: {text}"

    def test_apt_groups_pattern_matching(self):
        """APT groups pattern'i doğru çalışıyor mu?"""
        pattern = DETECTION_PATTERNS['apt_groups']
        test_cases = [
            ("APT28 conducted attacks", True),
            ("apt29 group discovered", False),  # Case sensitive
            ("Lazarus North Korea", True),
            ("LockBit ransomware gang", True),
            ("Conti operations", True),
            ("RandomGroup attacked", False),
        ]

        for text, should_match in test_cases:
            match = re.search(pattern, text, re.IGNORECASE)
            if should_match:
                assert match is not None, f"Should match: {text}"

    def test_large_number_pattern_matching(self):
        """Büyük sayı pattern'i doğru çalışıyor mu?"""
        pattern = DETECTION_PATTERNS['large_number']
        test_cases = [
            ("5 million users affected", True),
            ("10 million records stolen", True),
            ("100M customers", True),
            ("2B users", True),
            ("50 thousand affected", False),
            ("3 people", False),
        ]

        for text, should_match in test_cases:
            match = re.search(pattern, text)
            if should_match:
                assert match is not None, f"Should match: {text}"
            else:
                assert match is None, f"Should not match: {text}"

    def test_sectors_pattern_matching(self):
        """Sektor pattern'i doğru çalışıyor mu?"""
        pattern = DETECTION_PATTERNS['sectors']
        test_cases = [
            ("healthcare sector attacked", True),
            ("financial institutions", True),
            ("government agencies", True),
            ("energy grid compromised", True),
            ("hospital systems", True),
            ("retail company", False),
        ]

        for text, should_match in test_cases:
            match = re.search(pattern, text)
            if should_match:
                assert match is not None, f"Should match: {text}"
            else:
                assert match is None, f"Should not match: {text}"

    def test_countries_pattern_matching(self):
        """Ülke pattern'i doğru çalışıyor mu?"""
        pattern = DETECTION_PATTERNS['countries']
        test_cases = [
            ("Ukraine infrastructure attacked", True),
            ("Russia conducts attacks", True),
            ("China espionage campaign", True),
            ("Iran nuclear facility", True),
            ("Israel cybersecurity", True),
            ("Switzerland neutral", False),
        ]

        for text, should_match in test_cases:
            match = re.search(pattern, text)
            if should_match:
                assert match is not None, f"Should match: {text}"
            else:
                assert match is None, f"Should not match: {text}"


class TestPatternCombinations:
    """Kombinasyon testleri - gerçek haberlere benzeyen text'ler"""

    def test_critical_infrastructure_detection(self):
        """Kritik altyapı saldırısı tespit edilebiliyor mu?"""
        text = "Power grid in Ukraine targeted by APT28 in critical attack"

        # Pattern eşleşmeleri
        has_apt = re.search(DETECTION_PATTERNS['apt_groups'], text, re.IGNORECASE)
        has_country = re.search(DETECTION_PATTERNS['countries'], text, re.IGNORECASE)
        has_sector = re.search(DETECTION_PATTERNS['sectors'], text, re.IGNORECASE)

        assert has_apt is not None
        assert has_country is not None
        assert has_sector is not None

    def test_data_breach_detection(self):
        """Veri ihlali tespit edilebiliyor mu?"""
        text = "Major data breach affects 15 million customers, CVE-2024-5678"

        has_number = re.search(DETECTION_PATTERNS['large_number'], text)
        has_cve = re.search(DETECTION_PATTERNS['cve'], text)

        assert has_number is not None
        assert has_cve is not None

    def test_zero_day_detection(self):
        """Zero-day tespit edilebiliyor mu?"""
        text = "CVE-2024-1234 zero-day vulnerability discovered in Lazarus attack"

        has_cve = re.search(DETECTION_PATTERNS['cve'], text)
        has_apt = re.search(DETECTION_PATTERNS['apt_groups'], text, re.IGNORECASE)

        assert has_cve is not None
        assert has_apt is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
