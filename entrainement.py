import torch
import torch.nn as nn
import torch.optim as optim
import tiktoken
import time
from datasets import load_dataset

# 1. On importe notre moteur !
from apex_core import ApexCoreModel

# ==========================================
# 1. LE CHARGEUR DE DONNÉES MASSIVES
# ==========================================
class ChargeurDeDonnees:
    def __init__(self, nom_dataset="roneneldan/TinyStories", max_exemples=10000):
        print("📥 1. Connexion à HuggingFace et téléchargement des histoires...")
        self.dataset = load_dataset(nom_dataset, split=f"train[:{max_exemples}]")
        self.tokenizer = tiktoken.get_encoding("cl100k_base")
        
        print("✂️ 2. Traduction des histoires en nombres (Tokenisation)...")
        tous_les_tokens = []
        for ligne in self.dataset:
            tous_les_tokens.extend(self.tokenizer.encode(ligne["text"]))
            
        self.donnees_ia = torch.tensor(tous_les_tokens, dtype=torch.long)
        print(f"✅ Prêt ! Base de données : {len(self.donnees_ia):,} tokens.")

    def obtenir_batch(self, taille_batch, longueur_max):
        # Pioche des phrases au hasard pour l'entraînement
        limite_haute = len(self.donnees_ia) - longueur_max - 1
        indices = torch.randint(0, limite_haute, (taille_batch,))
        
        # X : Ce que l'IA lit
        x = torch.stack([self.donnees_ia[i : i + longueur_max] for i in indices])
        # Y : Ce que l'IA doit deviner (décalé d'un mot)
        y = torch.stack([self.donnees_ia[i + 1 : i + longueur_max + 1] for i in indices])
        return x, y

# ==========================================
# 2. LA BOUCLE D'ENTRAÎNEMENT PRINCIPALE
# ==========================================
def lancer_entrainement():
    print("\n==========================================")
    print("🚀 DÉMARRAGE DE L'APPRENTISSAGE APEX CORE")
    print("==========================================")
    
    # Configuration
    DIMENSION = 64
    LONGUEUR_MAX = 128
    TETES = 4
    COUCHES = 2
    TAILLE_BATCH = 8 
    ETAPES_TOTALES = 500 
    
    tokenizer = tiktoken.get_encoding("cl100k_base")
    chargeur = ChargeurDeDonnees()
    
    # Création du cerveau vierge
    modele = ApexCoreModel(
        vocab_size=tokenizer.n_vocab, d_model=DIMENSION, 
        max_seq_len=LONGUEUR_MAX, n_heads=TETES, n_layers=COUCHES
    )
    
    optimiseur = optim.AdamW(modele.parameters(), lr=0.001)
    calcul_erreur = nn.CrossEntropyLoss()
    
    modele.train()
    temps_debut = time.time()
    
    print("\n⏳ L'IA commence à lire et à apprendre...")
    for etape in range(ETAPES_TOTALES):
        x, y = chargeur.obtenir_batch(TAILLE_BATCH, LONGUEUR_MAX)
        
        optimiseur.zero_grad()
        scores = modele(x)
        perte = calcul_erreur(scores.view(-1, tokenizer.n_vocab), y.view(-1))
        
        perte.backward()
        optimiseur.step()
        
        if (etape + 1) % 50 == 0:
            temps_ecoule = time.time() - temps_debut
            print(f"Étape {etape + 1}/{ETAPES_TOTALES} | Erreur : {perte.item():.4f} | Temps : {temps_ecoule:.1f}s")
            
    print("\n✅ Entraînement terminé !")
    torch.save(modele.state_dict(), "apex_core_v1.pt")
    print("💾 Cerveau sauvegardé sous 'apex_core_v1.pt' !")

if __name__ == "__main__":
    lancer_entrainement()