# kbrd-api

> [!NOTE]
> TODO

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

KBRD-API relaie ces requêtes vers l'agent enregistré. Une inscription qui n'a
pas été renouvelée depuis 30 secondes est considérée comme inactive.

## Debug
/etc/init.d/S60kbrd-api stop
HOME=/home/kbrd /usr/bin/kbrd-api
