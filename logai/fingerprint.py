"""Deterministic message templating.

This is the cheap half of the pipeline and it runs on EVERY event.
Ten thousand log lines collapse into a few dozen templates, and only
the templates ever reach a model. No GPU is involved in this file.
"""
import hashlib
import re

# Order matters: most specific patterns first.
_RULES = [
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<UUID>"),
    (re.compile(r"\b(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}\b"), "<MAC>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b"), "<IP>"),
    (re.compile(r"\b(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}\b"), "<IPV6>"),
    # Interface names. Two safe shapes only:
    #   anything containing a slash  (Gi1/0/24, Te1/0/1, Serial0/0/0:0)
    #   known prefixes               (eth0, ens192, Vlan300, Port-channel2)
    # Deliberately NOT a generic letters+digits rule — that swallows tokens
    # like "ssh2" and "sha256" and produces misleading templates.
    (re.compile(r"\b[A-Za-z][A-Za-z\-]{1,24}\d+(?:/\d+)+(?::\d+)?\b"), "<IFACE>"),
    (re.compile(r"\b(?:Gi|Te|Fa|Xe|Et|Eth|Ethernet|GigabitEthernet|TenGigE|"
                r"eth|ens|eno|enp|em|bond|br|tun|tap|lo|vlan|Vlan|"
                r"Port-channel|po|Serial|Loopback|Tunnel)\d+\b"), "<IFACE>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<HEX>"),
    # Long hex identifiers: SCSI WWNs (naa.60060160...), session and object IDs,
    # git-style hashes. Requires >=8 chars AND at least one digit so ordinary
    # words are not swallowed. Without this, every storage LUN becomes its own
    # cluster and one issue fragments into dozens.
    (re.compile(r"\b(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{8,}\b"), "<HEX>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*"), "<TIME>"),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}\b"), "<TIME>"),
    (re.compile(r"-?\b\d+(?:\.\d+)?\s?(?:ms|us|ns|dBm|dB|MB|GB|TB|KB|kB|Mbps|Gbps|%|W|V)\b"), "<QTY>"),
    (re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])"), "<NUM>"),
    (re.compile(r"\s+"), " "),
]


def templatize(message: str) -> str:
    """Reduce a message to its structural template."""
    out = message.strip()
    for pattern, replacement in _RULES:
        out = pattern.sub(replacement, out)
    return out.strip()


def fingerprint(app: str, message: str) -> tuple[str, str]:
    """Return (fingerprint_hash, template).

    App name is part of the key so identical text from different daemons
    stays separate — 'connection refused' from sshd is not the same issue
    as 'connection refused' from a BGP process.
    """
    template = templatize(message)
    key = f"{(app or '-').lower()}::{template}"
    digest = hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:16]
    return digest, template
