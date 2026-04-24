---
name: ml-training
description: Launch, monitor, and manage ML training jobs on GPU — safe process handling, DataLoader-aware kill logic, VRAM/NaN/alpha alerts, QuickChart visualizations.
trigger_patterns:
  - "training"
  - "fine-tune"
  - "lora"
  - "sft"
  - "dpo"
  - "gpu job"
  - "monitor training"
tools_required:
  - bash
  - nvidia_smi
  - quickchart
examples:
  - "Launch SFT training on 474 KinéBot samples"
  - "Monitor PID 12345 training"
  - "Check nightly LoRA status"
model: qwen3.5-35b-a3b
execution:
  type: instructions_only
  command: null
  timeout_ms: null
  arg_mode: null
---

# ml-training — GPU Training Jobs

Skill d'instructions pour Claude/Qwen afin de lancer, monitorer et gérer des jobs d'entraînement ML sur GPU.

**Current base model**: Qwen3.5-35B-A3B (alias: qwen, provider: qwen35/qwen3.5-35b)

## Rules

1. **Launch**: Always use `setsid` + redirect stdout/stderr to file (`> log 2>&1`), never pipe through `tee`/`head`
2. **Monitor**: Read-only — `ps -p PID`, `nvidia-smi`, `tail log`. Never kill without explicit permission
3. **Process tree**: Before any kill, run `pstree -p PID` — DataLoader workers are children, not duplicates
4. **Unbuffered**: Always `python -u` for training scripts
5. **GPU check**: Before launching, verify `nvidia-smi` — only one training at a time
6. **Cron setup**: Create a monitoring cron (5 min interval) that checks PID alive + last log lines + VRAM + alert conditions (crash/NaN/α explosion)
7. **Charts**: Use QuickChart (localhost:3400) for training progress visualization

## Monitoring Cron Template

```
Check PID {PID}. Read last 5 lines of {LOG_PATH}. Check nvidia-smi VRAM.
IMPORTANT: Do NOT kill any process.
Report: (1) dead → ALERT, (2) α > 2.0 → ALERT, (3) NaN → ALERT, (4) brief status.
```

## Common Pitfalls

- `head -N` in a pipe blocks until N lines are written — never use with training output
- `nice -n 19` slows CPU preprocessing significantly — only use during GPU phase
- nohup + Python tqdm = broken pipe → use direct file redirect instead
- HuggingFace `load_dataset` re-downloads if cache is corrupted — check `~/.cache/huggingface/`
- PyYAML `safe_load` treats `3e-4` as string — use `0.0003` or `float()` cast
- `torch.cuda.amp` is deprecated — use `torch.amp`

## Typical Launch Pattern

```bash
cd /path/to/training
setsid python -u train.py --config config.yaml > /tmp/train_$(date +%Y%m%d_%H%M%S).log 2>&1 &
echo "PID: $!" > /tmp/train_pid.txt
```

Then monitor with `tail -f`, `nvidia-smi -l 5`, and a QuickChart endpoint for live loss curves.
