# CLAUDE.md — VaVáCoin (VVC)

Documento canônico do projeto. As decisões abaixo foram tomadas em conversa e
**não precisam ser relitigadas** — o que precisa de decisão está na seção
"Em aberto", separado de propósito.

**Estado: desenho fechado, nada implementado.**

---

## O que é

Economia digital simbólica da **turma ITA**. Moeda própria: **VaVáCoin (VVC)**.

É uma cópia em espírito da Sociedade Movida a Benbals, com o mesmo núcleo de
disciplina monetária, mas **sem a seriedade** dos BBL: aqui não há lastro, não
há dinheiro real e ninguém pode perder nada de valor.

**Não tem nenhuma relação com o ITA-IME Analytics.** Site diferente, código
diferente, banco diferente, propósito diferente. A única coisa em comum é o
público.

A graça não é acumular. É a camada social em cima — quem deve pro quem, quem
quebrou no cassino, quem virou o rico da sala.

---

## Regras econômicas invioláveis

- **Supply fixo em 5.000 VVC. Nunca cunhar.** Sem exceção, sem faucet
  automático.
- **Os 5.000 existem no banco central no dia zero**, todos de uma vez. O que
  ainda não foi sacado por ninguém fica lá como saldo não emitido — não é
  "dinheiro do BC", é dinheiro que ainda não entrou em circulação.
- **Quem entra SACA 50 do que já existe.** Não ganha 50 criados na hora — isso
  seria cunhar, e faria o supply crescer com o número de contas.
- O supply comporta **100 pessoas**. Se a turma passar disso, a decisão é
  reduzir o saque inicial, não emitir mais moeda.
- **Os 50 são da pessoa, não da conta.** Amarrados ao código de convite, um por
  aluno. Sem isso, dez contas viram 500 VVC.
- **Todo movimento de dinheiro passa por um único caminho** (`mover()`): trava a
  linha, conserva massa, crédito igual a débito. Nenhum caminho paralelo.
- **Exceção única: a gênese.** Alguém precisa fazer os 5.000 existirem, e antes
  disso não há de onde mover. `criar_genese()` é a única função que escreve
  saldo sem origem. Ela é blindada — só roda se o Banco Central ainda não
  existe, com ledger vazio e saldo zero — e a operação fica registrada no
  ledger como uma linha `genese` sem origem. **Isso não é bug**; está escrito
  aqui para o próximo leitor não "consertar".
- **Conservação de massa é verificável**: a soma de todos os saldos, incluindo
  o do banco central, é sempre **5.000,00**, antes e depois de qualquer
  operação. É o teste que roda em cima de qualquer feature que mexa em dinheiro.

### Conservação de massa não é o mesmo que auditoria

Descoberto ao construir o núcleo, e vale registrar porque é contraintuitivo:
**a soma continuar 5.000,00 não prova que ninguém mexeu.** Quem tira 10 de uma
conta e põe 10 em outra por fora do `mover()` conserva a massa perfeitamente.
A verificação de soma passa; a fraude fica.

Por isso existem duas checagens diferentes, e as duas precisam existir:

- **Conservação** (`verificar_conservacao`): a soma é 5.000,00. Barata, roda em
  cima de toda operação.
- **Auditoria** (`conferir_ledger`): reconstrói cada saldo a partir do zero
  somando o ledger, e compara com o saldo gravado. É o que acusa a troca
  disfarçada, porque nenhuma linha explica a mudança.

O teste que prova isso é `test_auditoria_acusa_troca_disfarcada_que_conserva_a_massa`.
Se alguém um dia achar a auditoria redundante com a conservação, é este
parágrafo e este teste que respondem.

### Por que manter supply fixo mesmo sem lastro

No Benbals a regra existe por causa do lastro em reais. Aqui o lastro não
existe, e a regra **sobrevive por outro motivo**: se dá para cunhar à vontade,
preço deixa de significar alguma coisa e status para de ser escasso. Registrado
porque a razão original não se aplica, e alguém no futuro pode concluir que a
regra também não.

---

## Consentimento

Princípio, não detalhe: **nada acontece com uma pessoa sem que ela tenha pedido.**

- **Cadastro manual.** Ninguém é inscrito automaticamente, nem importado de
  lista, nem criado a partir do ITA-IME Analytics.
- **Ranking sem obrigatoriedade.** Aparecer com o nome é escolha de cada um,
  inclusive de quem está em primeiro. Considerou-se obrigar os três primeiros a
  ficarem públicos; **descartado** por contradizer este princípio e por criar um
  incentivo torto — quem não quer aparecer perderia de propósito ou torraria
  dinheiro antes do corte.

---

## O que fica de fora, e por quê

O corte não é sobre a mecânica ser ruim. É sobre o contrato social ser outro:
entre onze amigos as pessoas consentiram sendo amigas; numa turma de setenta
ninguém consentiu em nada.

- **Multa e "roubar".** Entre amigos é piada; numa turma grande é tirar coisa de
  alguém com placar público, e encontra sozinho quem é mais fácil de mexer.
- **Títulos e Monarca.** Hierarquia pública explícita muda de sabor quando já
  existe um ranking de notas pairando sobre a sala.
- **Empresas, cotas, dividendos.** Não no dia zero. Talvez depois — ver
  "Em aberto".

---

## Cassino

**Fica.** Integra o Jogo do Caladinho, mines e o que mais existir, no site
oficial.

- **Dono: o autor do projeto** (decisão explícita, revisitável).
- **O saldo da casa e a vantagem são públicos**, sempre. O que é público vira
  personagem do jogo; o que é escondido vira suspeita que o dono não consegue
  desprovar depois — ainda mais sendo ele quem escreveu o jogo.

### O problema que o cassino cria, e como ele se resolve

Com supply fixo e nenhum faucet, **qualquer vantagem da casa termina do mesmo
jeito**: todo o dinheiro da turma na conta do dono. Não é risco, é aritmética;
só depende de quanto tempo.

Tirar o cassino do banco central muda **quem** acumula, não o fato de que
alguém acumula.

A saída escolhida: **o dono gastando é o faucet.** O cassino puxa dinheiro para
o dono, e o dono devolve à circulação pagando gente, comprando coisa e bancando
prêmio. O ralo vira ciclo, e o dono ganha um papel dentro do jogo em vez de ser
só o administrador. **Se o dono não gastar, a economia trava nele e acaba.**

Alternativas consideradas e não escolhidas, registradas para não serem
redescobertas: vantagem zero (o que entra sai integralmente como prêmio) e
separar autoria de propriedade (o autor escreve, outro jogador é o dono —
sorteado, leiloado ou rotativo).

---

## Ordem de implantação

**O cassino é a última coisa a ligar.**

Não é sobre o código: é que mines e cassino são exatamente a tela que o
coordenador do colégio não deveria ver enquanto a assessoria jurídica do GGE
analisa o ITA-IME Analytics. Os dois projetos são independentes, mas têm o mesmo
autor, os mesmos alunos e o mesmo mês — e quem olha de fora não separa.

Construir tudo; ligar o cassino depois da resposta.

---

## Decisões de escopo

**Não existe loja no site.** A moeda serve para pagar coisa da vida real entre
as pessoas — explicar uma questão, o lugar na fila, uma aposta boba — e o site
apenas registra a transferência. Mais fácil de construir e é o que já funciona
no Benbals.

Consequência a ter em mente: com isso, o **único sumidouro é o cassino** e o
**único faucet é o dono gastando**. Toda a saúde da economia depende desses
dois, e o segundo depende de uma pessoa lembrar de gastar.

**Sem temporada. A economia é contínua**, com reset se e quando fizer sentido.

O reset precisa existir como **operação de verdade** — devolve tudo ao banco
central e redistribui os 50 —, não como SQL improvisado no dia. Alterar saldo
por fora é exatamente como o Benbals ganhou o bug que faz saldo sumir. Se é
para poder resetar, o reset é uma feature, com teste de conservação de massa
antes e depois.

**Tudo volta ao Banco Central, sem exceção — inclusive o dono do cassino.**
Isso faz do reset o mecanismo real contra a concentração, no lugar da temporada
que foi descartada: o dinheiro acumula até alguém decidir que já chega.

**Os 50 são redistribuídos a quem tem convite resgatado**, não a quem tem conta.
Se fosse por conta, criar conta depois do reset viraria jeito de sacar de novo,
e o reset viraria faucet.

## Governança

O **Banco Central** é a autoridade do jogo: emite convite, cria conta, roda a
gênese e executa o reset. Não existe outro papel de administrador.

Cuidado que vem de erro já cometido no Benbals: o BC é ao mesmo tempo uma conta
de dinheiro e o poder administrativo. **Quem entrar nele é dono de tudo.** Lá,
contas de sistema autenticam com senha em texto puro e dá para esvaziar o caixa
de uma empresa entrando na conta dela. Aqui: senha com hash como qualquer conta,
e vale considerar que o BC simplesmente **não autentique pela tela** — os
poderes dele existem por CLI, que exige acesso ao servidor.

**Projeto novo, sem fork do Benbals.** Reaproveita os conceitos e a disciplina
do núcleo monetário, não os arquivos.

**Desenvolvimento local primeiro.** A publicação vai para uma segunda conta
gratuita do PythonAnywhere, criada quando o projeto estiver pronto para subir —
o plano gratuito dá um web app por conta e o Benbals já ocupa o primeiro.

---

## Em aberto

Nada bloqueando. As cinco decisões pendentes foram fechadas.

---

## Se for reaproveitar código do Benbals, corrigir antes

Dois bugs conhecidos, confirmados por leitura do código:

- **`delete_user` faz o saldo da pessoa sumir**, o que quebra o invariante de
  supply. Hoje ele não quebra na prática só porque estoura em erro de chave
  estrangeira antes de terminar, em dezoito tabelas. Quem "consertar" as FKs
  sem olhar isso destrava o vazamento.
- **Contas de tesouraria autenticam**, com senha em texto puro — dá para entrar
  na conta de uma empresa e esvaziar o caixa.

Herdar o núcleo monetário sem herdar esses dois é o mínimo.
