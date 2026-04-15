import torch
import os
from typing import Any, cast
from datasets import load_dataset
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import LoraConfig, get_peft_model
from trl.trainer.sft_trainer import SFTTrainer
from transformers import TrainingArguments

print("==========================================")
print("🚀 DÉMARRAGE DU FINE-TUNING APEX (AVEC LoRA)")
print("==========================================")

# 2026-04-15: default dataset updated to V4 for final training quality.
DATASET_FILE = os.getenv("APEX_DATASET_FILE", "dataset_expert_v4.json")

# Garde CPU : BitsAndBytes 4-bit requiert un GPU.
# Sur CPU seul, on valide uniquement la pipeline de données (smoke test partiel).
if not torch.cuda.is_available():
    print("⚠️  Pas de GPU détecté — validation de la pipeline de données uniquement.")
    from datasets import load_dataset as _ld
    _ds = _ld("json", data_files=DATASET_FILE, split="train")
    def _fmt(ex):
        return {"text": f"<|user|>\n{ex['instruction']}\n<|assistant|>\n{ex['output']}"}
    _ds_fmt = _ds.map(_fmt)
    print(f"\n✅ Dataset chargé : {len(_ds_fmt)} exemples")
    print("\n🔎 Premier prompt formaté (smoke test) :")
    print(_ds_fmt[0]["text"])
    print("\n✅ Pipeline data OK — lance le script sur une machine avec GPU pour l'entraînement complet.")
    raise SystemExit(0)

# 1. LE CHOIX DU MODÈLE DE BASE
# On aligne le fine-tuning sur la base déjà utilisée par l'adapter actuel.
nom_modele = os.getenv("APEX_BASE_MODEL", "unsloth/phi-4-unsloth-bnb-4bit")

# 2. LA COMPRESSION (4-bit)
# On compresse le modèle pour qu'il rentre dans ta carte graphique
quant_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_compute_dtype=torch.float16,
    bnb_4bit_quant_type="nf4"
)

print(f"📥 Téléchargement et compression de {nom_modele}...")
tokenizer = AutoTokenizer.from_pretrained(nom_modele)
tokenizer.pad_token = tokenizer.eos_token

modele_base = AutoModelForCausalLM.from_pretrained(
    nom_modele,
    quantization_config=quant_config,
    torch_dtype=torch.float16,
    device_map="auto",
    attn_implementation="eager",
)


def _detect_target_modules(model: Any) -> list[str]:
    names = [name for name, _ in model.named_modules()]
    if any(name.endswith("qkv_proj") for name in names):
        return ["qkv_proj", "o_proj"]
    if any(name.endswith("q_proj") for name in names) and any(name.endswith("v_proj") for name in names):
        return ["q_proj", "v_proj"]
    raise ValueError(
        "Impossible de detecter les target_modules LoRA compatibles. "
        "Ajoute un mapping explicite pour cette architecture."
    )


target_modules_lora = _detect_target_modules(modele_base)
print(f"🔧 Modules LoRA detectes: {target_modules_lora}")

# 3. LA CONFIGURATION LORA (La Clé USB de personnalité)
# On gèle le cerveau principal et on cible seulement certaines zones pour l'apprentissage
configuration_lora = LoraConfig(
    r=32,  # 4x plus grand que l'ancien (8→32) = LoRA plus expressif
    lora_alpha=32,  # alpha = 2*r pour un bon scaling
    target_modules=target_modules_lora, # On modifie l'Attention du modèle
    lora_dropout=0.05,
    task_type="CAUSAL_LM"
)

# On greffe la clé USB sur le modèle !
modele_apex = get_peft_model(modele_base, configuration_lora)
modele_apex.print_trainable_parameters() 
# Tu verras qu'on n'entraîne qu'environ 0.1% du modèle entier !

# 4. LES DONNÉES D'ENTRAÎNEMENT (Ce qu'on veut lui apprendre)
# Chargement du dataset local de distillation
dataset = load_dataset("json", data_files=DATASET_FILE, split="train")

def format_prompt(exemple):
    texte = f"<|user|>\n{exemple['instruction']}\n<|assistant|>\n{exemple['output']}"
    return {"text": texte}

dataset_formate = dataset.map(format_prompt)
print("\n🔎 Premier prompt formaté (smoke test):")
print(dataset_formate[0]["text"])

# 5. L'ENTRAÎNEMENT AUTOMATIQUE (Grâce à TRL)
# SFTTrainer (Supervised Fine-Tuning) gère toute la boucle compliquée qu'on codait à la main avant !
parametres_entrainement = TrainingArguments(
    output_dir="./apex_lora_sauvegarde",
    per_device_train_batch_size=1,
    gradient_accumulation_steps=4,
    learning_rate=2e-4,
    logging_steps=10,
    max_steps=10, # Smoke test rapide
    optim="paged_adamw_8bit"
)

entraineur = SFTTrainer(
    model=cast(Any, modele_apex),
    train_dataset=dataset_formate,
    args=parametres_entrainement,
)

print("\n⏳ Début de l'apprentissage LoRA...")
entraineur.train()

# 6. SAUVEGARDE DE LA "CLÉ USB"
print("\n✅ Entraînement terminé !")
nom_sauvegarde = "apex_lora_final"
cast(Any, modele_apex).save_pretrained(nom_sauvegarde)
print(f"💾 La personnalité Apex est sauvegardée dans le dossier '{nom_sauvegarde}' !")