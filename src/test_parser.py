# test_parser.py
from code_parser import encontrar_blocos_approx

with open('teste.cpp', 'r') as f:
    codigo = f.read()

blocos = encontrar_blocos_approx(codigo)
for inicio, fim, bloco in blocos:
    print("Bloco approx encontrado:")
    print(bloco)