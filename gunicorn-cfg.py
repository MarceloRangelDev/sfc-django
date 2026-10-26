# -*- encoding: utf-8 -*-

bind = '0.0.0.0:5005'
workers = 1
# Threads: cada tela da cozinha mantém um stream SSE aberto (apps/pedido/views.py); com worker síncrono ele bloquearia as demais requisições.
worker_class = 'gthread'
threads = 16
accesslog = '-'
loglevel = 'debug'
capture_output = True
enable_stdio_inheritance = True
