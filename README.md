# CELINA-STORE

Première boutique monoproduit-commerçant destinée au marché tunisien. Cette application locale est séparée de l'ERP CELINATECK existant.

## Fonctionnalités livrées

- Boutique publique responsive, recherche et filtre par catégorie.
- Panier de session et commande sans paiement en ligne (paiement à la livraison).
- Catalogue avec ajout de produits, gestion dédiée des catégories, jusqu'à 8 photos par produit, prix d'achat/de vente et seuils d'alerte.
- Promotions, historique client et journal consultable des mouvements de stock.
- Administration protégée par compte, mot de passe PBKDF2 et formulaires protégés par CSRF.
- Commandes avec états opérationnels simplifiés : nouvelle, confirmée, livrée ou refus/retour.
- Déduction du stock à la confirmation et restitution unique lors d'un refus ou d'un retour.
- Coûts publicitaires et d'emballage paramétrables par unité de produit, enregistrés en instantané lors de la commande.
- Frais fixes de retour configurables ; les statistiques estiment la marge après coût d'achat, publicité, emballage et dépenses liées aux retours.
- Bon livreur privé, imprimable au format A5 (une demi-page A4), avec coordonnées, articles et montant à encaisser.
- Journal de mouvements de stock et transactions SQLite (`BEGIN IMMEDIATE`).
- Tableau de bord et statistiques calculés à partir des commandes réellement livrées.
- Export CSV compatible Excel. Les chiffres sont stockés en millimes (entiers) et affichés en DT.
- Paramétrage des frais de livraison et commande CLI de sauvegarde SQLite.
- Produits de démonstration facultatifs, identifiés comme données de démo.

La marge réelle affichée est une estimation basée sur les coûts saisis : achat, publicité, emballage et retour. Les coûts publicitaires et d'emballage d'une commande refusée ou retournée, ainsi que le frais de retour fixé à la date du changement de statut, sont déduits ; le coût d'achat est exclu pour les produits dont le stock est réintégré. Les autres charges de l'entreprise ne sont pas incluses. L'export PDF, les notifications, l'envoi SMS et la gestion des autres dépenses ne sont pas inclus dans cette version.

## Prérequis Windows

- Python 3.11 ou plus récent.
- PowerShell.

## Installation

```powershell
cd "F:\celina store\celina_store"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:FLASK_SECRET_KEY = python -c "import secrets; print(secrets.token_hex(32))"
$env:FLASK_DEBUG = "1"
flask --app app init-db
flask --app app create-admin admin
```

La commande `create-admin` demande le mot de passe sans l'afficher. Utilisez un mot de passe propre à l'environnement. Pour afficher des produits de test, exécutez facultativement `flask --app app seed-demo`.
Pour sauvegarder la base, exécutez `flask --app app backup-db` ; le fichier horodaté est placé sous `instance/backups/`.

## Démarrage et arrêt

Double-cliquez sur `run.bat` pour démarrer la boutique sous Windows. Au premier lancement, le script crée l'environnement Python, installe les dépendances, génère et conserve une clé secrète locale puis initialise la base de données. Une connexion Internet est nécessaire la première fois pour installer Flask. Arrêtez le serveur avec `Ctrl+C`.

Vous pouvez aussi lancer l'application manuellement depuis PowerShell après l'installation :

```powershell
flask --app app run
```

Ouvrez `http://127.0.0.1:5000`. L'administration est à `http://127.0.0.1:5000/admin/login`. Arrêtez le serveur avec `Ctrl+C`.

Pour un usage hébergé, désactivez le mode debug, définissez une clé secrète persistante et aléatoire via l'environnement, configurez HTTPS, sauvegardez la base et placez les fichiers d'image sur un stockage adapté.

## Préparer le déploiement sur Render

Le fichier `render.yaml` configure un service web Render avec Gunicorn, HTTPS géré par Render, un contrôle de santé (`/healthz`) et un disque persistant monté sur `/var/data`. La base SQLite et les nouvelles photos produits y sont enregistrées. La boutique est publique ; les pages de gestion et les actions d'administration exigent une session administrateur. La page de connexion `/admin/login` reste naturellement accessible afin que l'administrateur puisse se connecter.

1. Ajouter ce dossier à un dépôt Git privé et le pousser sur GitHub. Le `.gitignore` exclut les environnements locaux, la base SQLite et les photos ; ne pas ajouter ces données ou un fichier `.env` au dépôt.
2. Dans Render, choisir **New > Blueprint**, connecter le dépôt et vérifier les paramètres et le coût du service avant de confirmer. Le Blueprint demande un plan `starter` et un disque persistant de 1 Go ; un service gratuit sans disque n'est pas adapté à cette configuration.
3. Après le premier déploiement, ouvrir le Shell du service Render et exécuter `flask --app app create-admin admin`. Saisir un mot de passe administrateur unique lorsqu'il est demandé ; ne pas mettre le mot de passe dans le dépôt ou dans une commande enregistrée.
4. Ouvrir l'URL publique du service. La boutique est accessible à tous ; aller sur `/admin/login` et se connecter pour gérer produits, commandes et paramètres.

Le premier déploiement crée une base SQLite vide sur le disque persistant. Il ne transfère pas automatiquement la base ni les photos présents sur le PC. Si les données actuelles doivent être conservées, effectuer leur migration vers le disque Render avant d'utiliser la boutique en production. Effectuer et vérifier des sauvegardes régulières ; le disque persistant protège des redéploiements mais ne remplace pas une sauvegarde externe. Avec ce choix SQLite/disque, ne pas augmenter le nombre d'instances Render : le disque n'est pas partagé entre plusieurs instances.

## Tests

Depuis ce dossier :

```powershell
python -m pytest -q
```

Les tests couvrent la commande publique, les instantanés, la connexion, CSRF, les transitions et restitutions du stock, les ajustements d'inventaire, les promotions, l'upload d'image, les ruptures, les ventes livrées, l'export CSV et le rendu des pages d'administration.
