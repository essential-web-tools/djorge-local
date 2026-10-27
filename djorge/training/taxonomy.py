"""Multilingual seed taxonomy for CLASS / ENTITY supervision.

Used ONLY to generate training/evaluation data. The runtime never consults it:
class membership at inference comes from the trained Qwen-derived model, which
is why held-out classes and held-out members exist (they measure whether the
model generalizes from Qwen's knowledge rather than memorizing this list).
Members include plural/inflected forms because real text is inflected.
"""
LANGS = ["en", "pt", "es", "fr", "de"]

T = {
    "Fruit": {"names": {"en": ["Fruit", "fruits"], "pt": ["Fruta"], "es": ["Fruta"], "fr": ["Fruit"], "de": ["Obst", "Frucht"]},
              "members": {"en": ["apple", "apples", "banana", "bananas", "orange", "oranges", "grape", "grapes", "mango", "pear", "cherries", "strawberry", "lemon", "peach", "kiwi", "papaya"],
                          "pt": ["maçã", "maçãs", "banana", "bananas", "laranja", "laranjas", "uva", "uvas", "manga", "pera", "morangos", "limão", "pêssego", "kiwi", "mamão"],
                          "es": ["manzana", "manzanas", "plátano", "plátanos", "naranja", "naranjas", "uva", "uvas", "mango", "pera", "fresas", "limón", "melocotón", "kiwi", "papaya"],
                          "fr": ["pomme", "pommes", "banane", "bananes", "orange", "oranges", "raisin", "mangue", "poire", "cerises", "fraise", "citron", "pêche", "kiwi"],
                          "de": ["Apfel", "Äpfel", "Banane", "Bananen", "Orange", "Trauben", "Mango", "Birne", "Kirschen", "Erdbeere", "Zitrone", "Pfirsich", "Kiwi"]},
              "heldout_members": ["kiwi", "papaya", "mamão", "pêssego", "melocotón", "Pfirsich", "pêche", "peach"]},
    "Vegetable": {"names": {"en": ["Vegetable"], "pt": ["Legume", "Verdura"], "es": ["Verdura"], "fr": ["Légume"], "de": ["Gemüse"]},
                  "members": {"en": ["carrot", "carrots", "potato", "potatoes", "onion", "lettuce", "spinach", "broccoli", "cucumber"],
                              "pt": ["cenoura", "cenouras", "batata", "batatas", "cebola", "alface", "espinafre", "brócolis", "pepino"],
                              "es": ["zanahoria", "zanahorias", "patata", "cebolla", "lechuga", "espinaca", "brócoli", "pepino"],
                              "fr": ["carotte", "carottes", "pomme de terre", "oignon", "laitue", "épinards", "brocoli", "concombre"],
                              "de": ["Karotte", "Karotten", "Kartoffel", "Zwiebel", "Salat", "Spinat", "Brokkoli", "Gurke"]}},
    "Animal": {"names": {"en": ["Animal"], "pt": ["Animal"], "es": ["Animal"], "fr": ["Animal"], "de": ["Tier"]},
               "members": {"en": ["dog", "dogs", "cat", "cats", "horse", "cow", "lion", "tiger", "elephant", "rabbit", "bird", "fox", "wolf"],
                           "pt": ["cachorro", "cachorros", "gato", "gatos", "cavalo", "vaca", "leão", "tigre", "elefante", "coelho", "pássaro", "raposa", "lobo"],
                           "es": ["perro", "perros", "gato", "gatos", "caballo", "vaca", "león", "tigre", "elefante", "conejo", "pájaro", "zorro", "lobo"],
                           "fr": ["chien", "chiens", "chat", "chats", "cheval", "vache", "lion", "tigre", "éléphant", "lapin", "oiseau", "renard", "loup"],
                           "de": ["Hund", "Hunde", "Katze", "Katzen", "Pferd", "Kuh", "Löwe", "Tiger", "Elefant", "Kaninchen", "Vogel", "Fuchs", "Wolf"]},
               "heldout_members": ["fox", "raposa", "zorro", "renard", "Fuchs", "wolf", "lobo", "loup", "Wolf"]},
    "Color": {"names": {"en": ["Color", "colour"], "pt": ["Cor"], "es": ["Color"], "fr": ["Couleur"], "de": ["Farbe"]},
              "members": {"en": ["red", "blue", "green", "yellow", "black", "white", "purple", "pink"],
                          "pt": ["vermelho", "azul", "verde", "amarelo", "preto", "branco", "roxo", "rosa"],
                          "es": ["rojo", "azul", "verde", "amarillo", "negro", "blanco", "morado", "rosa"],
                          "fr": ["rouge", "bleu", "vert", "jaune", "noir", "blanc", "violet", "rose"],
                          "de": ["rot", "blau", "grün", "gelb", "schwarz", "weiß", "lila", "rosa"]}},
    "Country": {"names": {"en": ["Country"], "pt": ["País"], "es": ["País"], "fr": ["Pays"], "de": ["Land"]},
                "members": {"en": ["Brazil", "Japan", "France", "Germany", "Canada", "Kenya", "India", "Mexico", "Portugal", "Egypt"],
                            "pt": ["Brasil", "Japão", "França", "Alemanha", "Canadá", "Quênia", "Índia", "México", "Portugal", "Egito"],
                            "es": ["Brasil", "Japón", "Francia", "Alemania", "Canadá", "Kenia", "India", "México", "Portugal", "Egipto"],
                            "fr": ["Brésil", "Japon", "France", "Allemagne", "Canada", "Kenya", "Inde", "Mexique", "Portugal", "Égypte"],
                            "de": ["Brasilien", "Japan", "Frankreich", "Deutschland", "Kanada", "Kenia", "Indien", "Mexiko", "Portugal", "Ägypten"]}},
    "Beverage": {"names": {"en": ["Beverage", "Drink"], "pt": ["Bebida"], "es": ["Bebida"], "fr": ["Boisson"], "de": ["Getränk"]},
                 "members": {"en": ["coffee", "tea", "water", "juice", "milk", "beer", "wine"],
                             "pt": ["café", "chá", "água", "suco", "leite", "cerveja", "vinho"],
                             "es": ["café", "té", "agua", "zumo", "leche", "cerveza", "vino"],
                             "fr": ["café", "thé", "eau", "jus", "lait", "bière", "vin"],
                             "de": ["Kaffee", "Tee", "Wasser", "Saft", "Milch", "Bier", "Wein"]}},
    "Profession": {"names": {"en": ["Profession", "Job"], "pt": ["Profissão"], "es": ["Profesión"], "fr": ["Métier"], "de": ["Beruf"]},
                   "members": {"en": ["doctor", "teacher", "engineer", "nurse", "lawyer", "farmer", "chef", "pilot"],
                               "pt": ["médico", "professora", "engenheiro", "enfermeira", "advogado", "agricultor", "cozinheiro", "piloto"],
                               "es": ["médico", "profesora", "ingeniero", "enfermera", "abogado", "agricultor", "cocinero", "piloto"],
                               "fr": ["médecin", "professeur", "ingénieur", "infirmière", "avocat", "agriculteur", "cuisinier", "pilote"],
                               "de": ["Arzt", "Lehrerin", "Ingenieur", "Krankenschwester", "Anwalt", "Bauer", "Koch", "Pilot"]}},
    "Clothing": {"names": {"en": ["Clothing"], "pt": ["Roupa"], "es": ["Ropa"], "fr": ["Vêtement"], "de": ["Kleidung"]},
                 "members": {"en": ["shirt", "shoes", "jacket", "dress", "hat", "socks", "jeans"],
                             "pt": ["camisa", "sapatos", "jaqueta", "vestido", "chapéu", "meias", "calça"],
                             "es": ["camisa", "zapatos", "chaqueta", "vestido", "sombrero", "calcetines", "pantalón"],
                             "fr": ["chemise", "chaussures", "veste", "robe", "chapeau", "chaussettes", "jean"],
                             "de": ["Hemd", "Schuhe", "Jacke", "Kleid", "Hut", "Socken", "Jeans"]}},
    # ---- held-out classes (never trained; zero-shot generalization test) ----
    "Vehicle": {"heldout_class": True, "names": {"en": ["Vehicle"], "pt": ["Veículo"], "es": ["Vehículo"], "fr": ["Véhicule"], "de": ["Fahrzeug"]},
                "members": {"en": ["car", "bus", "truck", "bicycle", "train", "motorcycle"],
                            "pt": ["carro", "ônibus", "caminhão", "bicicleta", "trem", "moto"],
                            "es": ["coche", "autobús", "camión", "bicicleta", "tren", "moto"],
                            "fr": ["voiture", "bus", "camion", "vélo", "train", "moto"],
                            "de": ["Auto", "Bus", "Lastwagen", "Fahrrad", "Zug", "Motorrad"]}},
    "Instrument": {"heldout_class": True, "names": {"en": ["Musical instrument"], "pt": ["Instrumento musical"], "es": ["Instrumento musical"], "fr": ["Instrument de musique"], "de": ["Musikinstrument"]},
                   "members": {"en": ["guitar", "piano", "violin", "drums", "flute"],
                               "pt": ["violão", "piano", "violino", "bateria", "flauta"],
                               "es": ["guitarra", "piano", "violín", "batería", "flauta"],
                               "fr": ["guitare", "piano", "violon", "batterie", "flûte"],
                               "de": ["Gitarre", "Klavier", "Geige", "Schlagzeug", "Flöte"]}},
}

# Distractor nouns that belong to none of the classes above.
NEUTRAL = {"en": ["table", "window", "pencil", "river", "cloud", "laptop", "door", "phone", "book", "key"],
           "pt": ["mesa", "janela", "lápis", "rio", "nuvem", "notebook", "porta", "telefone", "livro", "chave"],
           "es": ["mesa", "ventana", "lápiz", "río", "nube", "portátil", "puerta", "teléfono", "libro", "llave"],
           "fr": ["table", "fenêtre", "crayon", "rivière", "nuage", "ordinateur", "porte", "téléphone", "livre", "clé"],
           "de": ["Tisch", "Fenster", "Bleistift", "Fluss", "Wolke", "Laptop", "Tür", "Telefon", "Buch", "Schlüssel"]}

PEOPLE = ["Arthur", "Maria", "João", "Ana", "Carlos", "Sofia", "Lucas", "Emma", "Hiroshi", "Amara", "Pierre", "Lena"]
ORGS = ["Acme", "Globex", "Initech", "Petrobras", "Siemens", "Natura", "Telefónica", "Renault"]
PLACES = ["Lisboa", "São Paulo", "Madrid", "Paris", "Berlin", "Nairobi", "Tokyo", "Toronto"]

# Context-dependent hard cases: (lang, sentence, word, is_member_of_class, class)
HARD = [
    ("en", "Apple announced a new phone today.", "Apple", False, "Fruit"),
    ("en", "She ate an apple after lunch.", "apple", True, "Fruit"),
    ("en", "He bought a banana-colored shirt.", "banana", False, "Fruit"),
    ("en", "He painted the car orange.", "orange", False, "Fruit"),
    ("en", "He painted the car orange.", "orange", True, "Color"),
    ("en", "She squeezed an orange for juice.", "orange", True, "Fruit"),
    ("en", "Jaguar presented its new electric car.", "Jaguar", False, "Animal"),
    ("en", "A jaguar was seen near the river.", "jaguar", True, "Animal"),
    ("en", "We had turkey for dinner in Brazil.", "turkey", False, "Country"),
    ("pt", "A Apple lançou um novo telefone.", "Apple", False, "Fruit"),
    ("pt", "Ele comprou uma camisa cor de banana.", "banana", False, "Fruit"),
    ("pt", "Ela pintou a porta de laranja.", "laranja", False, "Fruit"),
    ("pt", "Ela pintou a porta de laranja.", "laranja", True, "Color"),
    ("pt", "Ele chupou uma laranja no almoço.", "laranja", True, "Fruit"),
    ("es", "Apple presentó un teléfono nuevo.", "Apple", False, "Fruit"),
    ("es", "Pintó la puerta de naranja.", "naranja", False, "Fruit"),
    ("fr", "Apple a présenté un nouveau téléphone.", "Apple", False, "Fruit"),
    ("de", "Apple stellte ein neues Telefon vor.", "Apple", False, "Fruit"),
]
