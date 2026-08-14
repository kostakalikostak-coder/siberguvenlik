"""
Sağlayıcı yedeği (provider fallback) testleri.

Doğrulanan davranış: OpenRouter birincilken TÜM modellerinde başarısız olursa
(kredi bitti / 402 / boş yanıt), aynı istem Google AI Studio (Gemini, ücretsiz
kota) üzerinden tekrar denenir. GEMINI_API_KEY yoksa eski davranış korunur:
çağrı None döner ve Gemini hiç denenmez.
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import main as main_mod
from src import config as cfg


def _kolektor():
    """__init__ çalıştırmadan sadece _gemini_call_json'ı kullanılabilir bir örnek."""
    cls = main_mod.HaberSistemi
    return cls.__new__(cls)


def _gemini_yanit(text):
    """google-genai generate_content dönüşünü taklit eder."""
    resp = MagicMock()
    cand = MagicMock()
    cand.finish_reason.name = 'STOP'
    resp.candidates = [cand]
    resp.text = text
    return resp


class TestGeminiFallback:

    @patch.object(main_mod, 'genai')
    @patch.object(main_mod._llm, 'generate_json')
    @patch.object(main_mod, 'is_openrouter_active', return_value=True)
    def test_openrouter_basarili_gemini_hic_cagrilmaz(self, _aktif, mock_or, mock_genai):
        mock_or.return_value = {'ok': 1}

        with patch.object(main_mod, 'GEMINI_API_KEY', 'test-key'):
            data = _kolektor()._gemini_call_json('istem', label='t')

        assert data == {'ok': 1}
        mock_genai.Client.assert_not_called()

    @patch('main.time.sleep')
    @patch.object(main_mod, 'genai')
    @patch.object(main_mod._llm, 'generate_json')
    @patch.object(main_mod, 'is_openrouter_active', return_value=True)
    def test_openrouter_coker_gemini_devralir(self, _aktif, mock_or, mock_genai, _sleep):
        """Kredi bitti senaryosu: OpenRouter None → Gemini yanıtı döner."""
        mock_or.return_value = None
        client = MagicMock()
        client.models.generate_content.return_value = _gemini_yanit('{"ok": 2}')
        mock_genai.Client.return_value = client

        with patch.object(main_mod, 'GEMINI_API_KEY', 'test-key'):
            data = _kolektor()._gemini_call_json('istem', label='t')

        assert data == {'ok': 2}
        mock_genai.Client.assert_called_once_with(api_key='test-key')
        # Yedekte ÖNCE yapılandırılmış ilk model denenmeli
        assert (client.models.generate_content.call_args.kwargs['model']
                == cfg.GEMINI_FALLBACK_MODELS[0])

    @patch('main.time.sleep')
    @patch.object(main_mod, 'genai')
    @patch.object(main_mod._llm, 'generate_json')
    @patch.object(main_mod, 'is_openrouter_active', return_value=True)
    def test_gemini_anahtari_yoksa_none(self, _aktif, mock_or, mock_genai, _sleep):
        """Eski davranış korunur: anahtar yoksa Gemini'ye hiç gidilmez."""
        mock_or.return_value = None

        with patch.object(main_mod, 'GEMINI_API_KEY', ''):
            data = _kolektor()._gemini_call_json('istem', label='t')

        assert data is None
        mock_genai.Client.assert_not_called()

    @patch('main.time.sleep')
    @patch.object(main_mod, 'genai')
    @patch.object(main_mod._llm, 'generate_json')
    @patch.object(main_mod, 'is_openrouter_active', return_value=True)
    def test_gemini_yedegi_de_coker(self, _aktif, mock_or, mock_genai, _sleep):
        """Her iki sağlayıcı da başarısız → None; yedek listesi kadar deneme yapılır."""
        mock_or.return_value = None
        client = MagicMock()
        client.models.generate_content.side_effect = Exception('429 quota')
        mock_genai.Client.return_value = client

        with patch.object(main_mod, 'GEMINI_API_KEY', 'test-key'):
            data = _kolektor()._gemini_call_json('istem', label='t')

        assert data is None
        assert client.models.generate_content.call_count == len(cfg.GEMINI_FALLBACK_MODELS)

    @patch('main.time.sleep')
    @patch.object(main_mod, 'genai')
    @patch.object(main_mod, 'is_openrouter_active', return_value=False)
    def test_provider_gemini_normal_sira(self, _aktif, mock_genai, _sleep):
        """LLM_PROVIDER=gemini iken GEMINI_MODELS sırası kullanılır."""
        client = MagicMock()
        client.models.generate_content.return_value = _gemini_yanit('{"ok": 3}')
        mock_genai.Client.return_value = client

        with patch.object(main_mod, 'GEMINI_API_KEY', 'test-key'):
            data = _kolektor()._gemini_call_json('istem', label='t')

        assert data == {'ok': 3}
        assert (client.models.generate_content.call_args.kwargs['model']
                == cfg.GEMINI_MODELS[0])


class TestConfigBayragi:

    def test_is_gemini_fallback_active(self):
        f = cfg.is_gemini_fallback_active
        with patch.object(cfg, 'LLM_PROVIDER', 'openrouter'), \
             patch.object(cfg, 'OPENROUTER_API_KEY', 'or-key'):
            with patch.object(cfg, 'GEMINI_API_KEY', 'g-key'):
                assert f() is True
            with patch.object(cfg, 'GEMINI_API_KEY', ''):
                assert f() is False
        # OpenRouter birincil değilse yedek kavramı yoktur
        with patch.object(cfg, 'LLM_PROVIDER', 'gemini'), \
             patch.object(cfg, 'GEMINI_API_KEY', 'g-key'):
            assert f() is False
