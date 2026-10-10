from polaris.config import Settings, get_settings, openai_base


def test_base_url_normalized_to_v1():
    assert openai_base("https://x.example.com") == "https://x.example.com/v1"
    assert openai_base("https://x.example.com/v1/") == "https://x.example.com/v1"
    assert openai_base("https://d.example.com/compatible-mode/v1") == "https://d.example.com/compatible-mode/v1"


def test_keys_never_leak():
    s = get_settings()
    dumped = repr(s) + str(s.public_summary())
    assert "sk-test-chat" not in dumped
    assert "sk-test-embed" not in dumped
    assert "test-ingest-token" not in dumped


def test_role_falls_back_to_default_model():
    s = Settings(_env_file=None, llm_base_url="http://a", llm_api_key="k", llm_model="base",
                 llm_model_extract="small")
    assert s.model_for("extract") == "small"
    assert s.model_for("chat") == "base"
    assert s.model_for("brief") == "base"


def test_embed_service_defaults_to_chat_service():
    s = Settings(_env_file=None, llm_base_url="http://a", llm_api_key="k", llm_model="m",
                 embed_base_url=None, embed_api_key=None)
    assert s.embed_base == "http://a/v1"
    assert s.embed_key.get_secret_value() == "k"
