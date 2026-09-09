# kbrd-api

> [!NOTE]
> TODO

## Documentation de l'API

Le contrat complet est décrit dans `src/kbrd_api/openapi.yaml`, servi par
`api/docs.py` :

|Route|Description|
|-|-|
|`GET /api/docs`|Swagger UI|
|`GET /api/openapi.yaml`|La spécification elle-même|

En développement : <http://127.0.0.1:8081/api/docs>. La page charge Swagger UI
depuis un CDN — c'est le *navigateur* qui a besoin d'un accès internet, pas le
clavier ; la spécification, elle, est servie localement et reste lisible sans.

La spécification est écrite à la main : les routes sont de simples closures
Flask, sans schémas à introspecter (voir l'en-tête du fichier). C'est
`tests/test_openapi.py` qui empêche la dérive — il parcourt l'`url_map` de
l'application et échoue sur toute route non documentée, tout chemin documenté
que plus aucune route ne sert, et toute méthode qui diffère entre les deux.
**Ajouter une route, c'est mettre à jour `openapi.yaml`.**

Ce test a besoin de PyYAML, qui n'est *pas* une dépendance d'exécution
(Swagger UI parse le YAML côté navigateur) :

    .venv/bin/pip install -e '.[test]'
    .venv/bin/python -m pytest tests

## KBRD Agent

L'agent desktop s'enregistre toutes les dix secondes sur
`POST /api/agent/register`. KBRD-WEB et KBRD-DEV utilisent ensuite uniquement
KBRD-API :

|Route|Description|
|-|-|
|`GET /api/agent`|État de la connexion de l'agent|
|`GET /api/applications`|Applications découvertes par l'agent|
|`POST /api/applications/<id>/launch`|Lance une application|
|`POST /api/applications/<id>/quit`|Quitte une application|
|`GET /api/browsers`|Navigateurs découverts par l'agent|
|`POST /api/browsers/<id>/open`|Ouvre une URL (`{"url": "..."}`) dans le navigateur|

KBRD-API relaie ces requêtes vers l'agent enregistré. Une inscription qui n'a
pas été renouvelée depuis 30 secondes est considérée comme inactive.

## KBRD-DEV

KBRD-DEV s'enregistre toutes les dix secondes sur `POST /api/device/register`
avec la résolution de son écran (`{"width": ..., "height": ...}`), sur le même
principe que l'agent desktop. `width_mm`/`height_mm` (taille physique en mm,
lue dans l'EDID — voir `kbrd_dev.edid`) sont envoyés en plus quand le panneau
les fournit ; sinon KBRD-API les renvoie à `null`.

|Route|Description|
|-|-|
|`GET /api/device`|État de la connexion et résolution du dernier KBRD-DEV enregistré|

Comme pour l'agent, une inscription qui n'a pas été renouvelée depuis
30 secondes est considérée comme inactive (`{"connected": false}`).

## Debug
/etc/init.d/S60kbrd-api stop
HOME=/home/kbrd /usr/bin/kbrd-api


## Development

Use this command to run `KBRD-API` without a Raspberry.

    ./dev.sh