---
name: verify
description: Self-consistency voting (N=3 samples) to reduce Qwen3.5 hallucinations. ~46% hallucination reduction per ACL 2025 research.
trigger_patterns:
  - "verify"
  - "vérifie"
  - "confirme"
  - "sûr de"
  - "double check"
  - "fact check"
tools_required:
  - exec
  - llm_qwen35
examples:
  - "Quelle est la capitale du Belize ?"
  - "17 moutons - 9 meurent, combien reste-t-il ?"
  - "Quel est le port de Grafana dans OpenClaw ?"
model: qwen3.5-35b-a3b
execution:
  command: "bash /home/remondiere/.openclaw/bin/self_consistency_wrapper.sh '$args'"
  timeout_ms: 240000
  arg_mode: raw
---

# verify — Self-Consistency Voting

Mécanisme de vote par auto-cohérence pour réduire les hallucinations de Qwen3.5-35B-A3B.

## Principle

1. Génère N réponses indépendantes (défaut: 3) à la même question
2. Utilise temperature=0.8 pour diversité
3. Extrait la réponse clé de chaque génération
4. Vote par similarité (exact match pour réponses courtes, clustering pour longues)
5. Retourne le consensus avec score de confiance

## Configuration

Variables d'environnement (optionnelles) :
- `SELF_CONSISTENCY_SAMPLES` : nombre d'échantillons (défaut: 3)
- `SELF_CONSISTENCY_TEMPERATURE` : température (défaut: 0.8)

## Output

- **Strong consensus (≥67%)** : `Consensus (3/3, 100%): Belmopan`
- **Weak consensus** : `No strong consensus (50%): ...` + liste des réponses divergentes
- **Error** : message d'erreur si aucune réponse valide

## Performance

- ~3-5s pour 3 échantillons (requêtes parallèles)
- Timeout : 180s (60s par requête)
- Réduit les hallucinations de ~46% selon recherche ACL 2025

## Use Cases

- Vérifier un fait douteux (dates, noms, lieux)
- Calculs arithmétiques simples
- Questions factuelles où Qwen3.5 peut halluciner
- Validation de ports/configs (alternative à `gateway`)
