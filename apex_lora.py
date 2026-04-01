import torch
from typing import Any, cast
from datasets import load_dataset
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import LoraConfig, get_peft_model
from trl.trainer.sft_trainer import SFTTrainer
from transformers import TrainingArguments

print("==========================================")
print("🚀 DÉMARRAGE DU FINE-TUNING APEX (AVEC LoRA)")
print("==========================================")

# 1. LE CHOIX DU MODÈLE DE BASE
# On prend TinyLlama (1.1 Milliard de paramètres) car il tourne sur presque tous les PC.
# (Tu pourras le remplacer par "mistralai/Mistral-7B-v0.1" si tu as un très gros PC plus tard)
nom_modele = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"

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
    device_map="auto"
)

# 3. LA CONFIGURATION LORA (La Clé USB de personnalité)
# On gèle le cerveau principal et on cible seulement certaines zones pour l'apprentissage
configuration_lora = LoraConfig(
    r=8, # La taille de notre "clé USB" (8 est un bon standard)
    lora_alpha=16,
    target_modules=["q_proj", "v_proj"], # On modifie l'Attention du modèle
    lora_dropout=0.05,
    task_type="CAUSAL_LM"
)

# On greffe la clé USB sur le modèle !
modele_apex = get_peft_model(modele_base, configuration_lora)
modele_apex.print_trainable_parameters() 
# Tu verras qu'on n'entraîne qu'environ 0.1% du modèle entier !

# 4. LES DONNÉES D'ENTRAÎNEMENT (Ce qu'on veut lui apprendre)
# Pour l'exemple, on télécharge un mini dataset de citations/instructions
dataset = load_dataset("Abirate/english_quotes", split="train[:500]")

def format_prompt(exemple):
    # On explique au modèle comment formuler sa réponse
    texte = f"<|user|>\nVoici une citation :\n<|assistant|>\n{exemple['quote']} - {exemple['author']}"
    return {"text": texte}

dataset_formate = dataset.map(format_prompt)

# 5. L'ENTRAÎNEMENT AUTOMATIQUE (Grâce à TRL)
# SFTTrainer (Supervised Fine-Tuning) gère toute la boucle compliquée qu'on codait à la main avant !
parametres_entrainement = TrainingArguments(
    output_dir="./apex_lora_sauvegarde",
    per_device_train_batch_size=1,
    gradient_accumulation_steps=4,
    learning_rate=2e-4,
    logging_steps=10,
    max_steps=10, # On fait juste 100 étapes pour ce test rapide
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