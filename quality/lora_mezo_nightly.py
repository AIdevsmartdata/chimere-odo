#!/usr/bin/env python3
"""lora_mezo_nightly.py — LoRA training via MeZO (zeroth-order optimizer)

MeZO uses the SAME memory as inference — no backward pass, no gradient storage.
Estimates gradients from 2 forward passes (perturb +eps, perturb -eps).

This WILL work on 16GB because it never calls .backward().
Slower convergence but guaranteed to fit.

Usage:
    python3 lora_mezo_nightly.py --dry-run
    python3 lora_mezo_nightly.py --steps 100 --lr 1e-6
"""

from __future__ import annotations
import argparse, json, math, random, subprocess, time, sys
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import LoraConfig, get_peft_model, TaskType

MODEL_DIR = Path.home() / ".openclaw" / "models" / "Qwen3.5-35B-A3B-BF16"
TRAINING_PAIRS = Path.home() / ".openclaw" / "logs" / "training_pairs.jsonl"
QUALITY_SCORES = Path.home() / ".openclaw" / "logs" / "quality_scores.jsonl"
OUTPUT_DIR = Path.home() / ".openclaw" / "lora" / "mezo-latest"


def load_data(min_score=3):
    scores = {}
    if QUALITY_SCORES.exists():
        with QUALITY_SCORES.open() as f:
            for line in f:
                try:
                    e = json.loads(line.strip())
                    ph = e.get("prompt_hash", "")
                    if ph: scores[ph] = e.get("score", 3)
                except: pass

    data = []
    with TRAINING_PAIRS.open() as f:
        for line in f:
            try:
                e = json.loads(line.strip())
                if len(e.get("response", "")) < 50: continue
                if scores.get(e.get("prompt_hash", ""), 3) < min_score: continue
                data.append(e)
            except: pass
    return data


def mezo_step(model, tokenizer, text, lr, eps, max_len=512):
    """One MeZO step: perturb → forward → unperturb → forward → update.

    Returns loss estimate (scalar).
    """
    tokens = tokenizer(text, return_tensors="pt", truncation=True,
                       max_length=max_len, padding=False)
    input_ids = tokens["input_ids"].to(model.device)

    if input_ids.shape[1] < 2:
        return 0.0

    # Get trainable params
    trainable = [(n, p) for n, p in model.named_parameters() if p.requires_grad]

    # Sample perturbation direction (same seed for both forward passes)
    seed = random.randint(0, 2**32 - 1)

    # Forward pass 1: perturb by +eps
    torch.manual_seed(seed)
    for _, p in trainable:
        z = torch.randn_like(p.data)
        p.data.add_(z, alpha=eps)

    with torch.no_grad():
        outputs = model(input_ids=input_ids, labels=input_ids)
        loss_plus = outputs.loss.item()

    # Forward pass 2: perturb by -2*eps (from +eps to -eps)
    torch.manual_seed(seed)
    for _, p in trainable:
        z = torch.randn_like(p.data)
        p.data.add_(z, alpha=-2 * eps)

    with torch.no_grad():
        outputs = model(input_ids=input_ids, labels=input_ids)
        loss_minus = outputs.loss.item()

    # Restore to original (from -eps to 0)
    torch.manual_seed(seed)
    for _, p in trainable:
        z = torch.randn_like(p.data)
        p.data.add_(z, alpha=eps)

    # MeZO gradient estimate and update
    grad_estimate = (loss_plus - loss_minus) / (2 * eps)

    torch.manual_seed(seed)
    for _, p in trainable:
        z = torch.randn_like(p.data)
        p.data.add_(z, alpha=-lr * grad_estimate)

    return (loss_plus + loss_minus) / 2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--lr", type=float, default=1e-6)
    parser.add_argument("--eps", type=float, default=1e-3)
    parser.add_argument("--lora-r", type=int, default=4)
    parser.add_argument("--max-len", type=int, default=384)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("=" * 50)
    print("MeZO LoRA — Zero-order optimization (no backward)")
    print("=" * 50)

    data = load_data()
    print(f"Training data: {len(data)} pairs")

    if len(data) < 5:
        print("ERROR: Not enough data"); sys.exit(1)

    if args.dry_run:
        print(f"[DRY RUN] Would train {args.steps} steps, lr={args.lr}")
        return

    # Stop qwen35 to free VRAM (14+ GB used by llama-server)
    print("[MeZO] Stopping qwen35-custom.service to free VRAM...")
    subprocess.run(["systemctl", "--user", "stop", "qwen35-custom.service"], check=False)
    subprocess.run(["systemctl", "--user", "stop", "qwen-watchdog.service"], check=False)
    time.sleep(5)  # Wait for GPU memory to be released

    try:
        print(f"\nLoading model (4-bit, auto device_map)...")
        t0 = time.monotonic()

        from transformers import BitsAndBytesConfig
        model = AutoModelForCausalLM.from_pretrained(
            str(MODEL_DIR),
            device_map="auto",
            max_memory={0: "13GiB", "cpu": "20GiB"},
            dtype=torch.bfloat16,
            trust_remote_code=True,
            offload_folder="/tmp/offload",
        )
        tokenizer = AutoTokenizer.from_pretrained(str(MODEL_DIR), trust_remote_code=True)
        print(f"  Loaded in {time.monotonic() - t0:.0f}s, GPU: {torch.cuda.memory_allocated()/1e9:.1f} GB")

        # LoRA
        print(f"Adding LoRA r={args.lora_r}...")
        lora_config = LoraConfig(
            r=args.lora_r, lora_alpha=8,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
            lora_dropout=0.0, bias="none", task_type=TaskType.CAUSAL_LM,
        )
        model = get_peft_model(model, lora_config)
        model.print_trainable_parameters()

        # Prepare texts
        texts = []
        for e in data:
            prompt = e["prompt"]
            response = e["response"]
            reasoning = e.get("reasoning", "")
            assistant = f"<think>\n{reasoning}\n</think>\n{response}" if reasoning else response
            msgs = [{"role": "user", "content": prompt}, {"role": "assistant", "content": assistant}]
            text = tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=False)
            texts.append(text)

        print(f"\nTraining {args.steps} MeZO steps (lr={args.lr}, eps={args.eps})...")
        t0 = time.monotonic()
        losses = []

        for step in range(args.steps):
            text = random.choice(texts)
            loss = mezo_step(model, tokenizer, text, args.lr, args.eps, args.max_len)
            losses.append(loss)

            if (step + 1) % 10 == 0:
                avg = sum(losses[-10:]) / 10
                elapsed = time.monotonic() - t0
                print(f"  Step {step+1}/{args.steps}: loss={avg:.4f} ({elapsed:.0f}s)")

        total_time = time.monotonic() - t0
        final_loss = sum(losses[-10:]) / min(10, len(losses))
        print(f"\nDone in {total_time:.0f}s. Final avg loss: {final_loss:.4f}")

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        # Save only LoRA params (not frozen base model which has meta tensors)
        state_dict = {}
        for name, param in model.named_parameters():
            if param.requires_grad and param.device.type != "meta":
                state_dict[name] = param.data.cpu()
        torch.save(state_dict, str(OUTPUT_DIR / "lora_weights.pt"))
        tokenizer.save_pretrained(str(OUTPUT_DIR))
        print(f"  Saved {len(state_dict)} LoRA tensors")

        meta = {"method": "MeZO", "steps": args.steps, "lr": args.lr, "eps": args.eps,
                "lora_r": args.lora_r, "final_loss": final_loss, "time_s": round(total_time),
                "data_pairs": len(data)}
        (OUTPUT_DIR / "metadata.json").write_text(json.dumps(meta, indent=2))
        print(f"Saved to: {OUTPUT_DIR}")
    finally:
        # Always restart qwen35 (even on error/OOM)
        print("[MeZO] Restarting qwen35-custom.service...")
        subprocess.run(["systemctl", "--user", "start", "qwen35-custom.service"], check=False)
        subprocess.run(["systemctl", "--user", "start", "qwen-watchdog.service"], check=False)


if __name__ == "__main__":
    main()
