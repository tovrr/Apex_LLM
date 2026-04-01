import json
from pathlib import Path


def make_math(i: int) -> dict[str, str]:
    a = i + 2
    b = i + 5
    c = i + 7
    instruction = (
        f"Maths avancees #{i}: Resoudre le systeme lineaire suivant et verifier la solution: "
        f"2x + {a}y = {b}, 3x - y = {c}."
    )
    output = (
        "<think>\n"
        "Je pose le systeme sous forme standard et j'isole une variable depuis la deuxieme equation.\n"
        "Ensuite je substitue dans la premiere equation pour obtenir une equation a une inconnue.\n"
        "Je calcule y, puis je remplace pour retrouver x.\n"
        "Je termine par une verification numerique des deux equations pour eviter une erreur de signe.\n"
        "</think>\n"
        "Reponse finale: la paire (x, y) est obtenue par substitution; verification effectuee sur les deux equations."
    )
    return {"instruction": instruction, "output": output}


def make_algo(i: int) -> dict[str, str]:
    n = 10 + i
    instruction = (
        f"Algorithmique #{i}: Ecrire une fonction Python qui retourne les {n} plus petits elements d'une "
        "liste en O(n log k) avec un heap, et expliquer la complexite."
    )
    output = (
        "<think>\n"
        "On veut maintenir une structure de taille k pendant un parcours lineaire des donnees.\n"
        "Un max-heap de taille k conserve les k plus petits vus jusqu'ici.\n"
        "A chaque nouvel element, on compare avec la racine puis on remplace si utile.\n"
        "Chaque operation est en log(k), donc cout global O(n log k).\n"
        "Je donne ensuite une implementation Python claire et testable.\n"
        "</think>\n"
        "Reponse finale: utiliser un heap borne a k elements donne O(n log k), avec memoire O(k)."
    )
    return {"instruction": instruction, "output": output}


def make_logic(i: int) -> dict[str, str]:
    instruction = (
        f"Logique pure #{i}: Trois affirmations A, B, C sont proposees, exactement une est vraie. "
        "Deduis l'etat vrai/faux de chaque affirmation en justifiant."
    )
    output = (
        "<think>\n"
        "Je teste les cas possibles en imposant la contrainte 'exactement une vraie'.\n"
        "Pour chaque cas, je compte le nombre d'affirmations vraies et j'elimine les contradictions.\n"
        "Je conserve uniquement les affectations qui respectent la contrainte globale.\n"
        "Je choisis la solution unique restante et je la valide avec une verification finale.\n"
        "</think>\n"
        "Reponse finale: une seule affectation est coherente; elle satisfait strictement la contrainte d'unicite."
    )
    return {"instruction": instruction, "output": output}


def build_dataset() -> list[dict[str, str]]:
    data: list[dict[str, str]] = []
    for i in range(1, 41):
        data.append(make_math(i))
    for i in range(1, 41):
        data.append(make_algo(i))
    for i in range(1, 21):
        data.append(make_logic(i))
    return data


def main() -> None:
    out_path = Path("dataset_expert.json")
    dataset = build_dataset()
    out_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written={out_path}")
    print(f"count={len(dataset)}")


if __name__ == "__main__":
    main()
