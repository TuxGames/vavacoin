/* O avião subindo, igual em todas as telas.
 *
 * ESTE ARQUIVO NÃO DECIDE NADA. Ele desenha um número. Quem diz onde a curva
 * para é o servidor, e quem resolve a aposta é o alvo declarado junto com ela
 * — não existe botão de sacar, e é justamente por isso que o navegador pode
 * saber a curva inteira sem que isso vire dinheiro de graça.
 *
 * COMO ELE SABE A CURVA SEM FICAR PERGUNTANDO: a rodada é o relógio
 * (`floor(epoch / duração)`), então o número dela, quando a janela de aposta
 * fecha e quando o ciclo acaba saem de uma conta local. A única coisa que
 * precisa vir do servidor é o ponto de estouro — UM pedido por rodada, feito
 * no instante em que a janela fecha, porque antes disso o servidor se recusa
 * a contar (quem soubesse onde estoura apostaria só quando fosse favorável).
 *
 * O RELÓGIO DO CELULAR NÃO É CONFIÁVEL. Ele pode estar minutos fora, e aí a
 * tela desenharia outra rodada. Por isso toda resposta traz o `agora` do
 * servidor e o que vale aqui é o desvio medido contra ele.
 *
 * SEM ESTE ARQUIVO A TELA CONTINUA HONESTA: o servidor já entrega o
 * formulário fechado quando não dá para apostar, e o "Atualizar" leva para a
 * janela seguinte. O que se perde é a animação, não o jogo.
 *
 * Arquivo próprio e `addEventListener` porque a CSP é `script-src 'self'`:
 * não passa `<script>` com corpo nem `onload=` no HTML.
 */
(function () {
  "use strict";

  /* O `jogo.js` troca o miolo da página depois de uma aposta, e com ele vão
     embora todos os elementos. Por isso nada é guardado entre um quadro e
     outro: cada passada procura de novo. */
  function raiz() {
    return document.getElementById("crash");
  }

  var desvio = 0; /* relógio do servidor menos o do navegador, em ms */
  var conhecido = {}; /* número da rodada -> ponto de estouro já revelado */
  var pedindo = null; /* rodada cujo estouro está sendo buscado agora */
  var ultimas = null; /* a fileira de resultados, como veio do servidor */
  var recarregando = false;

  function agora() {
    return Date.now() + desvio;
  }

  function numeros(no) {
    var duracao = parseFloat(no.dataset.crashDuracao);
    var janela = parseFloat(no.dataset.crashJanela);
    var dobrar = parseFloat(no.dataset.crashDobrar);
    if (!(duracao > 0) || !(dobrar > 0)) return null;
    return { duracao: duracao, janela: janela, dobrar: dobrar };
  }

  function anotarDesvio(servidorMs) {
    if (isFinite(servidorMs)) desvio = servidorMs - Date.now();
  }

  function buscar(no, numero) {
    if (pedindo === numero || !window.fetch) return;
    pedindo = numero;
    fetch(no.dataset.crashCiclo, {
      headers: { Accept: "application/json" },
      credentials: "same-origin"
    })
      .then(function (r) {
        if (!r.ok) throw new Error("resposta " + r.status);
        return r.json();
      })
      .then(function (dados) {
        anotarDesvio(dados.agora);
        if (dados.estouro) {
          conhecido[dados.numero] = parseFloat(dados.estouro);
        }
        if (dados.ultimas) ultimas = dados.ultimas;
        pedindo = null;
      })
      .catch(function () {
        /* Rede ruim não pode travar a tela para sempre: solta o pedido e a
           próxima passada tenta de novo. Enquanto não vier, o avião sobe e
           não explode — e o resultado de verdade chega no recarregar, porque
           quem resolve a aposta é o servidor. */
        pedindo = null;
      });
  }

  function desenharUltimas(no) {
    if (!ultimas) return;
    var caixa = no.querySelector("#crash-ultimas");
    if (!caixa) return;
    var atual = caixa.getAttribute("data-assinatura");
    var assinatura = ultimas.join(",");
    if (atual === assinatura) return;
    caixa.setAttribute("data-assinatura", assinatura);
    caixa.textContent = "";
    ultimas.forEach(function (valor) {
      var pastilha = document.createElement("span");
      pastilha.className =
        "cal-pastilha " +
        (parseFloat(valor) >= 2 ? "cal-pastilha-alta" : "cal-pastilha-baixa");
      pastilha.textContent = valor + "×";
      caixa.appendChild(pastilha);
    });
  }

  function passada() {
    var no = raiz();
    if (!no) return;
    var n = numeros(no);
    if (!n) return;

    /* Com script, o "Atualizar" não serve para nada: a tela se vira sozinha. */
    no.classList.add("crash-com-script");

    var t = agora() / 1000;
    var numero = Math.floor(t / n.duracao);
    var abre = numero * n.duracao;
    var voa = abre + n.janela;
    var podeApostar = t < voa;

    var visor = no.querySelector("#crash-visor");
    var mostrador = no.querySelector("#crash-numero");
    var relogio = no.querySelector("#crash-relogio");
    var campos = no.querySelector("#crash-campos");

    /* A janela abre e fecha sozinha. Sem isto o botão continuaria clicável
       depois de o avião levantar, e a aposta bateria no servidor para ser
       recusada — o que é correto, mas é o dedo batendo em porta fechada. */
    var minhaAberta = !!no.dataset.crashMinha;
    if (campos) campos.disabled = !podeApostar || minhaAberta;

    if (podeApostar) {
      if (mostrador) mostrador.textContent = "1.00";
      if (visor) visor.classList.add("cal-crash-parado");
      if (relogio) {
        relogio.textContent = "#" + numero + " · " + Math.ceil(voa - t) + "s";
      }
      /* Uma busca já na janela: traz a fileira de resultados atualizada e
         acerta o relógio antes de o avião levantar. O estouro não vem — o
         servidor se recusa —, e é isso que se quer. */
      if (conhecido[numero] === undefined && pedindo === null) buscar(no, numero);
      desenharUltimas(no);
      return;
    }

    var estouro = conhecido[numero];
    if (estouro === undefined) {
      /* A janela fechou: agora o servidor conta. */
      buscar(no, numero);
    }

    var decorridos = t - voa;
    var valor = Math.pow(2, decorridos / n.dobrar);
    var explodiu = estouro !== undefined && valor >= estouro;

    if (explodiu) {
      if (mostrador) mostrador.textContent = estouro.toFixed(2);
      if (visor) visor.classList.add("cal-crash-parado");
      if (relogio) {
        relogio.textContent =
          "#" + numero + " · " + Math.ceil(abre + n.duracao - t) + "s";
      }
      /* Quem apostou nesta rodada precisa ver o próprio resultado e o saldo
         novo, e os dois moram no servidor. Uma recarga por aposta — a mesma
         viagem que o POST já fazia antes. */
      if (no.dataset.crashMinha === String(numero) && !recarregando) {
        recarregando = true;
        window.setTimeout(function () {
          window.location.reload();
        }, 1200);
      }
    } else {
      if (mostrador) mostrador.textContent = valor.toFixed(2);
      if (visor) visor.classList.remove("cal-crash-parado");
      if (relogio) relogio.textContent = "#" + numero;
    }
    desenharUltimas(no);
  }

  var inicial = raiz();
  if (!inicial) return;
  anotarDesvio(parseFloat(inicial.dataset.crashAgora));

  /* ~30 quadros por segundo: suave o bastante para parecer animação e leve o
     bastante para o celular da turma. */
  window.setInterval(passada, 33);
  passada();
})();
