"""Gera o hash bcrypt da senha do painel para colocar em PAINEL_SENHA_HASH no .env.

Uso:
    python -m sistema_forex.scripts.gerar_hash
    python -m sistema_forex.scripts.gerar_hash "minha senha"
"""

import getpass
import sys

import bcrypt


def main() -> None:
    if len(sys.argv) > 1:
        senha = sys.argv[1]
    else:
        senha = getpass.getpass("Senha do painel: ")
    if not senha:
        print("Senha vazia — abortado.")
        raise SystemExit(1)
    h = bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    print("\nColoque no .env (sem aspas):\n")
    print(f"PAINEL_SENHA_HASH={h}")


if __name__ == "__main__":
    main()
