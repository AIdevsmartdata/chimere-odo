---
name: ioc-analyze
description: Threat intelligence analysis of IoCs (IPs, domains, hashes, URLs) via CyberBro (33 engines including Shodan/AbuseIPDB/VirusTotal) + Brave Search enrichment.
trigger_patterns:
  - "ioc"
  - "analyze ip"
  - "suspicious domain"
  - "hash analysis"
  - "threat intel"
  - "virustotal"
tools_required:
  - exec
  - cyberbro
  - search_router
examples:
  - "185.220.101.34"
  - "suspicious-domain.xyz"
  - "d41d8cd98f00b204e9800998ecf8427e"
  - "https://malware-c2.example.com/beacon"
model: qwen3.5-35b-a3b
execution:
  command: "bash /home/remondiere/.openclaw/bin/ioc_analyze.sh '$args'"
  timeout_ms: 180000
  arg_mode: raw
---

# ioc-analyze — IoC Threat Intelligence

Analyse automatique d'observables (IP, domaines, hashes, URLs) via CyberBro + Brave Search.

## Process

1. **Submit to CyberBro** — 33 moteurs dont Shodan, AbuseIPDB, VirusTotal, IPQualityScore
2. **Wait for results** — ~15s pour agrégation des verdicts
3. **Brave enrichment** — Contexte CTI (rapports récents, TTPs, campagnes)
4. **Structured report** — Verdict de menace + sources

## Supported Observables

- **IPv4/IPv6** : géolocalisation, ASN, ports ouverts (Shodan), réputation (AbuseIPDB)
- **Domains** : WHOIS, DNS, réputation (VirusTotal, URLhaus)
- **MD5/SHA1/SHA256 hashes** : détection AV (VirusTotal), familles de malware
- **URLs** : scan (URLScan, VirusTotal), catégorisation
- **CVEs** : NVD + exploits connus

## Output

Rapport structuré avec :
- Verdict : Clean / Suspicious / Malicious (+ niveau de confiance)
- Engine scores agrégés
- Indicateurs associés (pivot)
- Contexte CTI (campagnes, acteurs, TTPs)

## Performance

- Total : ~30-90s (dominé par CyberBro fetch + Brave)
- Timeout : 180s
