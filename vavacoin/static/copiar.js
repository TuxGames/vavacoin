/* Copiar o link do convite, no painel do Banco Central.

   Arquivo próprio e `addEventListener` porque a CSP é `script-src 'self'`:
   não passa `onclick=` no HTML nem `<script>` com corpo. O que o botão vai
   copiar vem do atributo `data-copiar`, que é dado, não código.

   O `navigator.clipboard` só existe em contexto seguro (https, ou localhost
   no desenvolvimento). Em produção o PythonAnywhere serve por https, então
   é o caminho normal; o `<textarea>` fora da tela é a rede de segurança para
   navegador velho ou http, e existe porque um botão de copiar que não copia
   é pior que botão nenhum.

   Se nada funcionar, o link continua escrito na tela e selecionável — o
   botão é conveniência, nunca a única forma de pegar o link. */
(function () {
  var AVISO = 1600; /* quanto tempo o botão fica dizendo "Copiado" */

  function copiarPeloTextarea(texto) {
    var campo = document.createElement("textarea");
    campo.value = texto;
    campo.className = "fora-da-tela";
    campo.setAttribute("readonly", "readonly");
    document.body.appendChild(campo);
    campo.select();
    var deu = false;
    try {
      deu = document.execCommand("copy");
    } catch (e) {
      deu = false;
    }
    document.body.removeChild(campo);
    return deu;
  }

  function avisar(botao, texto) {
    if (botao.dataset.rotulo === undefined) {
      botao.dataset.rotulo = botao.textContent;
    }
    botao.textContent = texto;
    window.clearTimeout(botao._volta);
    botao._volta = window.setTimeout(function () {
      botao.textContent = botao.dataset.rotulo;
    }, AVISO);
  }

  function copiar(botao) {
    var texto = botao.getAttribute("data-copiar");
    if (!texto) return;

    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(texto).then(
        function () {
          avisar(botao, "Copiado");
        },
        function () {
          avisar(botao, copiarPeloTextarea(texto) ? "Copiado" : "Selecione");
        }
      );
      return;
    }
    avisar(botao, copiarPeloTextarea(texto) ? "Copiado" : "Selecione");
  }

  document.querySelectorAll("button.copiar[data-copiar]").forEach(function (botao) {
    botao.addEventListener("click", function () {
      copiar(botao);
    });
  });
})();
