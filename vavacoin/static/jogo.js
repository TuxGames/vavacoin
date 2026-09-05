/* Troca só o miolo da tela a cada jogada, em vez de recarregar a página.
 *
 * O QUE ISTO NÃO MUDA: nada da mecânica. O resultado continua sendo sorteado e
 * decidido no servidor, a rodada continua resolvendo uma vez só, e o dinheiro
 * continua passando pelo mesmo caminho. Aqui só muda o transporte — o mesmo
 * POST, com um cabeçalho a mais, e a resposta já vem sendo a tela pronta em
 * vez de um redirect que obriga uma segunda viagem.
 *
 * POR QUE: cada clique era POST + redirect + GET, duas idas ao servidor e uma
 * página inteira remontada. A turma está no Brasil e o servidor não; no plano
 * grátis há um worker só. Metade das viagens é metade da espera.
 *
 * SEM JAVASCRIPT CONTINUA FUNCIONANDO. Este arquivo só intercepta depois de
 * carregar; se ele não carregar, falhar ou o `fetch` der erro, o formulário
 * submete sozinho como sempre fez, e o servidor responde com o redirect de
 * antes. O caminho antigo é o padrão, não a exceção.
 */
(function () {
  "use strict";

  var area = document.getElementById("conteudo");
  if (!area || !window.fetch || !window.DOMParser || !window.FormData) {
    return;
  }

  /* Enquanto uma jogada está no ar, o painel fica marcado. Dois cliques
     seguidos na mesma casa não podem virar duas jogadas — o servidor já
     recusa a segunda (a rodada resolve uma vez só), mas deixar o dedo
     clicar e não acontecer nada é pior do que travar a tela por um instante. */
  var ocupado = false;

  function jogoNaTela() {
    return area.querySelector("[data-jogo]");
  }

  function trocar(html) {
    var novo = new DOMParser()
      .parseFromString(html, "text/html")
      .getElementById("conteudo");
    if (!novo) {
      return false;
    }
    area.replaceWith(novo);
    area = novo;
    return true;
  }

  function limparEndereco() {
    /* Depois da jogada, o endereço volta para o do jogo. Sem isso, um
       `?nova=1` ou um `?rodada=` velho continuaria na barra e um F5 mostraria
       outra coisa que não o que está na tela. */
    var alvo = jogoNaTela();
    if (alvo && window.history && window.history.replaceState) {
      window.history.replaceState({}, "", alvo.getAttribute("data-jogo"));
    }
  }

  area.addEventListener("submit", function (evento) {
    var forma = evento.target;
    if (!(forma instanceof HTMLFormElement)) {
      return;
    }
    /* Só POST de dentro do jogo. O seletor de minas é um GET e continua
       navegação normal. */
    if (forma.method.toLowerCase() !== "post" || !forma.closest("[data-jogo]")) {
      return;
    }
    if (ocupado) {
      evento.preventDefault();
      return;
    }

    evento.preventDefault();
    ocupado = true;
    area.setAttribute("aria-busy", "true");

    /* O botão apertado é um campo como outro qualquer: sem ele, "revelar casa
       7" chegaria sem o 7. O FormData não inclui o submitter sozinho. */
    var dados = new FormData(forma);
    var apertado = evento.submitter;
    if (apertado && apertado.name) {
      dados.append(apertado.name, apertado.value);
    }

    fetch(forma.action, {
      method: "POST",
      body: dados,
      headers: { "X-VavaCoin-Parcial": "1" },
      credentials: "same-origin",
      redirect: "follow"
    })
      .then(function (resposta) {
        if (!resposta.ok) {
          throw new Error("resposta " + resposta.status);
        }
        return resposta.text();
      })
      .then(function (html) {
        if (!trocar(html)) {
          throw new Error("sem miolo na resposta");
        }
        limparEndereco();
        ocupado = false;
      })
      .catch(function () {
        /* Qualquer tropeço — rede caiu, sessão expirou, jogo desligado no
           meio — volta para o caminho de sempre: manda o formulário de
           verdade e deixa o servidor responder a página inteira. A jogada
           não se perde e não acontece duas vezes: se a primeira chegou, a
           rodada já está resolvida e o servidor mostra o resultado dela. */
        ocupado = false;
        forma.submit();
      });
  });
})();
