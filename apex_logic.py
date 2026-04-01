import torch
import tiktoken
from apex_core import ApexCoreModel, generer_texte

class MoteurApexLogic:
    def __init__(self, modele, tokenizer):
        self.modele = modele
        self.tokenizer = tokenizer
        
        # Les balises secrètes de réflexion
        self.balise_debut = "<think>\n"
        self.balise_fin = "\n</think>\n"

    def generer_avec_reflexion(self, question, budget_reflexion=15, mots_reponse=15):
        """
        Gère le cycle complet : Question -> Réflexion cachée -> Réponse finale.
        """
        print(f"\n👤 Utilisateur : {question}")
        print("⚙️ [Apex Logic s'active...]")
        
        # ---------------------------------------------------------
        # ÉTAPE 1 : LA RÉFLEXION (Le brouillon)
        # ---------------------------------------------------------
        prompt_brouillon = f"Question: {question}\n{self.balise_debut}"
        
        # On fait générer le brouillon par le modèle de base
        texte_avec_brouillon = generer_texte(
            self.modele, 
            self.tokenizer, 
            prompt_brouillon, 
            mots_a_generer=budget_reflexion
        )
        
        # On extrait uniquement les mots de la pensée pour te les montrer dans la console
        pensee_interne = texte_avec_brouillon.replace(prompt_brouillon, "").strip()
        print(f"   💭 [Pensée interne] : {pensee_interne}...")

        # ---------------------------------------------------------
        # ÉTAPE 2 : LA RÉPONSE FINALE
        # ---------------------------------------------------------
        # On reprend tout le texte, on ferme la balise, et on demande la conclusion
        prompt_final = f"{texte_avec_brouillon}{self.balise_fin}Réponse finale :"
        
        reponse_complete = generer_texte(
            self.modele, 
            self.tokenizer, 
            prompt_final, 
            mots_a_generer=mots_reponse
        )
        
        # On isole la toute dernière partie pour l'utilisateur
        reponse_utilisateur = reponse_complete.split("Réponse finale :")[-1].strip()
        
        print("\n💬 Apex Logic :")
        return reponse_utilisateur

# ==========================================
# TEST DU MODE THINK
# ==========================================
if __name__ == "__main__":
    print("--- Lancement du module Apex Logic ---")
    
    # 1. On charge les outils
    mon_tokenizer = tiktoken.get_encoding("cl100k_base")
    mon_modele = ApexCoreModel(vocab_size=mon_tokenizer.n_vocab, d_model=64, max_seq_len=128, n_heads=4, n_layers=2)
    
    # On essaie de charger notre cerveau entraîné (si tu l'as généré à l'étape précédente)
    try:
        mon_modele.load_state_dict(torch.load("apex_core_v1.pt"))
        print("✅ Cerveau Apex Core chargé.")
    except FileNotFoundError:
        print("⚠️ Cerveau non trouvé. Le modèle va générer des mots au hasard.")
    
    # 2. On connecte le mode Logic au modèle de base
    moteur_logic = MoteurApexLogic(mon_modele, mon_tokenizer)
    
    # 3. On pose une question complexe
    question_test = "Si j'ai 3 pommes et que j'en mange 1, combien m'en reste-t-il ?"
    
    reponse_finale = moteur_logic.generer_avec_reflexion(
        question=question_test, 
        budget_reflexion=10, # On lui donne 10 mots pour réfléchir
        mots_reponse=10      # On lui donne 10 mots pour répondre
    )
    
    print(reponse_finale)