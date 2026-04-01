import torch
import torch.nn as nn
import torch.nn.functional as F
import tiktoken

# ==========================================
# 1. LE MOTEUR OPTIMISÉ (FlashAttention)
# ==========================================
class AttentionRapide(nn.Module):
    """
    Remplace l'attention classique. Utilise F.scaled_dot_product_attention 
    pour être 3x plus rapide et consommer beaucoup moins de mémoire.
    """
    def __init__(self, d_model, n_heads):
        super().__init__()
        self.n_heads = n_heads
        self.d_head = d_model // n_heads
        
        # On génère Query, Key et Value en une seule opération mathématique (très rapide)
        self.qkv_proj = nn.Linear(d_model, 3 * d_model)
        self.out_proj = nn.Linear(d_model, d_model)

    def forward(self, x):
        batch_size, seq_len, d_model = x.size()
        
        # 1. Projection Q, K, V
        qkv = self.qkv_proj(x)
        
        # 2. Découpage pour les multiples "têtes" d'attention
        qkv = qkv.view(batch_size, seq_len, 3, self.n_heads, self.d_head)
        qkv = qkv.permute(2, 0, 3, 1, 4) 
        q, k, v = qkv[0], qkv[1], qkv[2]
        
        # 3. La magie de PyTorch (FlashAttention natif)
        # is_causal=True empêche de tricher en lisant les mots futurs
        attention_out = F.scaled_dot_product_attention(q, k, v, is_causal=True)
        
        # 4. Réassemblage
        attention_out = attention_out.permute(0, 2, 1, 3).contiguous().view(batch_size, seq_len, d_model)
        return self.out_proj(attention_out)

# ==========================================
# 2. LE BLOC TRANSFORMER (Le "Neurone" Géant)
# ==========================================
class BlocApexCore(nn.Module):
    """
    Combine le travail d'équipe (Attention) et le travail individuel (Feed-Forward).
    """
    def __init__(self, d_model, n_heads):
        super().__init__()
        self.attention = AttentionRapide(d_model, n_heads)
        self.norm1 = nn.LayerNorm(d_model)
        
        self.ffn = nn.Sequential(
            nn.Linear(d_model, d_model * 4),
            nn.GELU(),
            nn.Linear(d_model * 4, d_model)
        )
        self.norm2 = nn.LayerNorm(d_model)

    def forward(self, x):
        # Raccourcis résiduels (x + ...) pour préserver l'information originelle
        x = x + self.attention(self.norm1(x))
        x = x + self.ffn(self.norm2(x))
        return x

# ==========================================
# 3. LE MODÈLE COMPLET (Apex Core)
# ==========================================
class ApexCoreModel(nn.Module):
    """
    L'architecture principale. Gère le sens, la position, l'analyse et la parole.
    """
    def __init__(self, vocab_size, d_model, max_seq_len, n_heads, n_layers):
        super().__init__()
        # A. Dictionnaires de Sens et de Position
        self.token_embedding = nn.Embedding(vocab_size, d_model)
        self.pos_embedding = nn.Embedding(max_seq_len, d_model)
        
        # B. Empilement des blocs d'intelligence
        self.blocs = nn.ModuleList([
            BlocApexCore(d_model, n_heads) for _ in range(n_layers)
        ])
        
        # C. Tête de Langage (Pour transformer la pensée en mots)
        self.norm_finale = nn.LayerNorm(d_model)
        self.lm_head = nn.Linear(d_model, vocab_size, bias=False)

    def forward(self, x):
        longueur_phrase = x.size(1)
        positions = torch.arange(0, longueur_phrase, dtype=torch.long, device=x.device)
        
        # 1. Compréhension initiale
        x = self.token_embedding(x) + self.pos_embedding(positions)
        
        # 2. Réflexion à travers toutes les couches
        for bloc in self.blocs:
            x = bloc(x)
            
        x = self.norm_finale(x)
        
        # 3. Prédiction des probabilités du vocabulaire
        return self.lm_head(x)

# ==========================================
# 4. OUTIL DE GÉNÉRATION (Faire parler l'IA)
# ==========================================
def generer_texte(modele, tokenizer, phrase_depart, mots_a_generer=10):
    """
    Boucle autorégressive : devine le mot suivant, l'ajoute à la phrase, et recommence.
    """
    modele.eval()
    tokens_actuels = tokenizer.encode(phrase_depart)
    vecteur_tokens = torch.tensor([tokens_actuels], dtype=torch.long)
    
    with torch.no_grad():
        for _ in range(mots_a_generer):
            scores = modele(vecteur_tokens)
            scores_dernier_mot = scores[0, -1, :] 
            
            # Transformation en probabilités et choix du meilleur mot
            probabilites = F.softmax(scores_dernier_mot, dim=-1)
            token_choisi = torch.argmax(probabilites).item()
            
            nouveau_token_tenseur = torch.tensor([[token_choisi]], dtype=torch.long)
            vecteur_tokens = torch.cat((vecteur_tokens, nouveau_token_tenseur), dim=1)

    return tokenizer.decode(vecteur_tokens[0].tolist())

# ==========================================
# 5. TEST DE VÉRIFICATION DU FICHIER
# ==========================================
if __name__ == "__main__":
    print("--- Audit d'Apex Core V1.0 (Optimisé) ---")
    mon_tokenizer = tiktoken.get_encoding("cl100k_base")
    
    # Configuration Fast Standard
    modele = ApexCoreModel(
        vocab_size=mon_tokenizer.n_vocab, 
        d_model=64, 
        max_seq_len=128, 
        n_heads=4, 
        n_layers=2
    )
    
    # Calcul du nombre de paramètres (Comme les pros !)
    total_params = sum(p.numel() for p in modele.parameters())
    print(f"✅ Architecture validée.")
    print(f"🧠 Taille du cerveau : {total_params:,} paramètres.")
    print("⚡ FlashAttention : Activé.")
    
    print("\nTest de génération (Modèle non-entraîné) :")
    texte = generer_texte(modele, mon_tokenizer, "Le futur de l'IA est", mots_a_generer=5)
    print(f"Résultat : {texte}")