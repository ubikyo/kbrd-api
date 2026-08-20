TARGET := kbrd

REMOTE_DIR := /usr/lib/python3.14/site-packages/kbrd_api
SERVICE := /etc/init.d/S60kbrd-api

.PHONY: deploy restart

deploy:
	@printf "\033[47;30m %-60s \033[0m\n" " KBRD-API : déploiement "
	rsync -av --delete \
		--exclude='__pycache__/' \
		--exclude='*.pyc' \
		src/kbrd_api/ \
		$(TARGET):$(REMOTE_DIR)/

	@printf "\033[47;30m %-60s \033[0m\n" " KBRD-API : redémarrage du service "
	ssh $(TARGET) "$(SERVICE) restart"

restart:
	@printf "\033[47;30m %-60s \033[0m\n" " KBRD-API : redémarrage du service "
	ssh $(TARGET) "$(SERVICE) restart"