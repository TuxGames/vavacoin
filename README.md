# VaVáCoin

Economia simbólica da turma. Supply fixo de 5.000 VVC, que ninguém cunha.
As decisões do projeto estão no `CLAUDE.md` e não se relitigam aqui.

**O que já existe:** o núcleo monetário, a auditoria e a web mínima — entrar
por convite, ver o próprio saldo e extrato, transferir com confirmação, e a
economia inteira pública. **O que não existe:** cassino, ranking e qualquer
administração por tela (administração é só CLI).

## Ambiente

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
```

## Rodar local

```bash
export FLASK_APP=wsgi.py
flask db upgrade
flask genese
flask convite --destinatario "Fulano"   # imprime o código; entregue à pessoa
flask run
```

A pessoa entra em `/cadastro` com o código, escolhe usuário e senha, e sai de
lá com os 50 VVC sacados do Banco Central.

| rota | o que é |
| --- | --- |
| `/` | o que é a moeda |
| `/economia` | estado da economia, público: emitido, em circulação, saldo do BC |
| `/cadastro` | única porta de entrada, e só com convite |
| `/entrar`, `/sair` | login com Flask-Login |
| `/carteira` | seu saldo e seu extrato (só o seu) |
| `/transferir` | monta a transferência; **não move nada** |
| `/transferir/confirmar` | mostra valor e destinatário, e só então efetiva |

## Testes

```bash
.venv/Scripts/python.exe -m pytest
```

Toda operação é cercada pelo helper `conservacao()` (em `testes/conftest.py`):
a soma de **todos** os saldos, incluindo o do Banco Central, é sempre
exatamente 5.000,00.

## Banco e operação

```bash
export FLASK_APP=wsgi.py
flask db upgrade                      # cria as tabelas
flask genese                          # 5.000,00 no Banco Central (idempotente)
flask convite --destinatario "Fulano" # emite um convite; imprime o código
flask criar-conta fulano              # pede a senha sem eco
flask extrato fulano                  # o que entrou e saiu, com saldo em cada linha
flask conservacao                     # confere a massa
flask auditoria                       # massa + o ledger explica cada saldo (sai 1 se não)
flask resetar                         # recolhe de todos e redistribui os 50
```

Estes são os poderes do Banco Central, e é de propósito que só existam aqui:
exercê-los exige acesso ao servidor.

`conservacao` e `auditoria` respondem coisas diferentes, e a diferença importa.
A soma continuar 5.000,00 não prova nada sozinha — quem tira de um e põe no
outro por fora do `mover()` conserva a massa. `auditoria` reconstrói todo saldo
a partir do ledger e acusa a diferença; tem teste exatamente desse caso.

## Onde está o quê

| arquivo | o que guarda |
| --- | --- |
| `vavacoin/dinheiro.py` | tipo monetário: `Decimal` em Python, centavos inteiros no banco |
| `vavacoin/moeda.py` | `mover()` — o caminho único —, a gênese e a verificação de massa |
| `vavacoin/operacoes.py` | resgate do convite, transferência, reset |
| `vavacoin/auditoria.py` | extrato, estado da economia, reconstrução do ledger |
| `vavacoin/autoridade.py` | o Banco Central é o único administrador |
| `vavacoin/modelos.py` | `Usuario`, `Convite`, `Transacao` (ledger) |
| `vavacoin/constantes.py` | supply, saque inicial, capacidade |

## Regras que o código impõe

- Nenhuma função fora de `mover()` escreve em saldo. A exceção é a gênese,
  que só roda com o ledger vazio e sem Banco Central.
- Dinheiro nunca é `float` — `para_decimal()` recusa, não converte.
- Nada de `commit` dentro da biblioteca: quem chama é dono da transação. As
  operações compostas rodam em `SAVEPOINT` para não deixar meio trabalho.
- Senha com hash bcrypt.
- **O Banco Central não tem porta de entrada.** Ele é conta de dinheiro e poder
  administrativo ao mesmo tempo, então quem entrasse nele seria dono de tudo —
  o erro que o Benbals cometeu com contas de tesouraria. Fechado em cinco
  camadas, cada uma com teste: não tem senha (`definir_senha` recusa e o banco
  tem `CHECK`), não é `is_active`, o `user_loader` o descarta, o `get_id()`
  estoura (fecha o `login_user(..., force=True)`), e uma conta que já tem senha
  não pode ser promovida a BC.
- **Poder se pede explicitamente.** Criar conta, emitir convite e resetar exigem
  `autoridade=banco_central()`; não basta conseguir importar a função. A
  exceção é o cadastro pelo site, onde a autoridade chega **delegada pelo
  convite** — foi o BC que o emitiu, e ele vale uma conta só.
- **CSRF em todo formulário** (Flask-WTF), e **CSP restrita desde a primeira
  rota**: sem `unsafe-inline`, sem estilo inline, sem `on*=`, sem script. Um
  teste varre os templates e falha se algum entrar — apertar a CSP depois é o
  tipo de retrabalho que nunca acontece.
- **Transferência tem confirmação.** O que executa é o que o servidor guardou
  na sessão e desenhou na tela, não o que voltou no formulário: o confirmado
  não pode divergir do mostrado. A confirmação expira em 10 minutos.
- **Rate limit no login**, por IP + usuário. É em memória: some no restart e
  não é compartilhado entre processos. Para um worker resolve; se um dia
  houver mais de um, vira tabela.

## Publicar no PythonAnywhere

Conta `vavacoin` (o Benbals já ocupa o web app da outra conta). Deploy por git,
como no ITA-IME. **Nenhum passo abaixo foi executado** — quem sobe é o autor.

1. **Console Bash** na conta `vavacoin`:

   ```bash
   git clone https://github.com/TuxGames/vavacoin.git vavacoin
   cd vavacoin
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

2. **Segredo e ambiente.** Gere uma chave e guarde-a fora do repositório:

   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```

   Guarde-a onde ela não volte para o git. O caminho confiável no
   PythonAnywhere é definir as duas variáveis **no arquivo WSGI do painel,
   antes do import** (ver passo 4); o campo *Environment variables* da aba
   Web não alcança os consoles, então repita o `export` no Bash quando for
   rodar comandos.

   Sem `VAVACOIN_SECRET_KEY`, com `VAVACOIN_ENV=producao`, a aplicação
   **se recusa a subir** — a chave padrão é pública, está aqui no repositório.

3. **Banco:**

   ```bash
   export FLASK_APP=wsgi.py VAVACOIN_ENV=producao VAVACOIN_SECRET_KEY=<a chave>
   .venv/bin/flask db upgrade
   .venv/bin/flask genese
   .venv/bin/flask conservacao   # deve dizer 5000.00
   ```

4. **Web app:** *Web → Add a new web app → Manual configuration → Python 3*.
   - *Source code*: `/home/vavacoin/vavacoin`
   - *Virtualenv*: `/home/vavacoin/vavacoin/.venv`
   - No arquivo WSGI do painel, apontar para este projeto:

     ```python
     import os
     import sys

     sys.path.insert(0, "/home/vavacoin/vavacoin")
     os.environ["VAVACOIN_ENV"] = "producao"
     os.environ["VAVACOIN_SECRET_KEY"] = "<a chave gerada no passo 2>"

     from wsgi import application  # noqa: E402,F401
     ```

     O arquivo WSGI do painel fica fora do repositório, então a chave não
     corre risco de ser commitada.

   - *Static files*: URL `/static/` → `/home/vavacoin/vavacoin/vavacoin/static`

5. **Convites:** `flask convite --destinatario "Fulano"` por aluno, um cada.

6. **Depois de subir:** `flask auditoria` deve sair com código 0. Vale deixar
   rodando periodicamente — é o que acusa saldo escrito fora do `mover()`.

O banco fica em `/home/vavacoin/vavacoin/vavacoin.sqlite3`, dentro do clone
mas ignorado pelo git — `git pull` não encosta nele.

Atualizar: `git pull && .venv/bin/flask db upgrade` e *Reload* no painel.
