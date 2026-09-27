import pytest

from nima_semantica.models import ConfigurationError
from nima_semantica.providers import ModelManifest, validate_manifest


@pytest.mark.parametrize("parameters", [{"api_key": "secret"}, {"nested": {"Authorization": "secret"}}, {"list": [{"password": "secret"}]}])
def test_credentials_cannot_enter_manifests(parameters):
    with pytest.raises(ConfigurationError):
        validate_manifest(ModelManifest(provider="test", model="test", revision="v1", parameters=parameters))


def test_token_limits_are_not_mistaken_for_credentials():
    validate_manifest(ModelManifest(provider="test", model="test", revision="v1", parameters={"max_tokens": 100}))
