"""The harness HTTP client: pipeline/net.py's client under the harness policy, which allows the study backends, logged to results/requests.log."""
from pipeline import config, net, policy


def client(**kwargs):
    return net.Client(policy.harness_refusal, config.ROOT / "results" / "requests.log", **kwargs)
