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
- Journal de mouvements de stock et transactions SQLite en local ou PostgreSQL sur Supabase en production.
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

## Déploiement gratuit sur Render avec Supabase

Le Blueprint `render.yaml` configure un service web Render Free, Gunicorn, HTTPS et le contrôle de santé `/healthz`. Render Free n'a pas de disque persistant : la base PostgreSQL et les photos doivent donc rester sur Supabase. Le service utilise Supabase PostgreSQL et le bucket Supabase Storage `product-images`. Les pages de boutique et les images sont publiques ; les pages et actions d'administration restent protégées par connexion.

### 1. Préparer Supabase

1. Créer un projet Supabase Free et conserver en lieu sûr le mot de passe de base de données choisi.
2. Dans **Storage**, créer le bucket `product-images` et le déclarer public afin que les visiteurs puissent afficher les photos. Les téléversements utilisent la clé de service côté serveur ; ne jamais la placer dans le navigateur ou dans le dépôt.
3. Dans **Project Settings > API**, relever l'URL du projet et la clé `service_role` (ou la clé secrète équivalente affichée par le tableau de bord).
4. Dans **Project Settings > Database**, copier la chaîne de connexion PostgreSQL fournie par Supabase. Préférer le pooler de session si l'URL directe n'est pas joignable depuis Render. Remplacer le mot de passe d'exemple dans cette URL par le vrai mot de passe, en encodant les caractères spéciaux si nécessaire.

### 2. Déployer le Blueprint

1. Le dépôt privé `unkut931/celina-store` contient le projet. Dans Render, choisir **New > Blueprint**, connecter ce dépôt et sélectionner la branche `master`.
2. Avant de confirmer, vérifier que le service web est bien sur le plan **Free** et qu'aucun disque ou service payant n'est ajouté.
3. Render demandera les valeurs secrètes `DATABASE_URL`, `SUPABASE_URL` et `SUPABASE_SERVICE_ROLE_KEY`. Les saisir dans Render uniquement. Le nom du bucket `product-images` est déjà configuré par le Blueprint. Ne pas enregistrer les valeurs secrètes dans Git ou dans une conversation.
4. Laisser le premier déploiement terminer, puis ouvrir l'URL Render. La route `/healthz` doit répondre ; vérifier la boutique publique et que `/admin/` redirige vers la page de connexion.

### 3. Créer le compte administrateur

Après le déploiement, ouvrir le Shell du service sur Render et exécuter `flask --app app create-admin admin`. Saisir un mot de passe administrateur unique lorsqu'il est demandé ; ne pas le mettre dans le dépôt ou dans une commande enregistrée. Se connecter ensuite à `/admin/login`.

Le schéma de base est initialisé au démarrage. Les produits, photos, clients et commandes présents sur le PC ne sont pas transférés automatiquement ; il faut décider séparément si les données existantes doivent être migrées. En particulier, ne téléverser aucune donnée personnelle de clients ou de commandes sans autorisation et procédure de migration appropriées.

**Limites de l'offre gratuite :** Render peut mettre le service en veille après une période d'inactivité, avec un délai de réveil à la prochaine visite. Le stockage local Render est éphémère : conserver toutes les photos importantes dans Supabase Storage. Supabase Free applique ses propres quotas et peut mettre en pause les projets inactifs ; consulter les limites et conditions actuelles dans les tableaux de bord des deux services, et effectuer des sauvegardes régulières de la base.

## Tests

Depuis ce dossier :

```powershell
python -m pytest -q
```

Les tests couvrent la commande publique, les instantanés, la connexion, CSRF, les transitions et restitutions du stock, les ajustements d'inventaire, les promotions, l'upload d'image, les ruptures, les ventes livrées, l'export CSV et le rendu des pages d'administration.
