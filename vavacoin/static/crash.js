/* A curva do crash subindo na tela.

   ESTE ARQUIVO NÃO DECIDE NADA. Ele desenha um número. Quem sabe onde a curva
   para é o servidor, e o ponto de estouro nunca chega aqui — o elemento só
   recebe há quanto tempo a rodada começou e de quantos em quantos segundos o
   multiplicador dobra.

   Se o JavaScript não rodar, o número fica parado no valor que o servidor
   escreveu e o jogo continua jogável: o alvo declarado na aposta resolve
   sozinho, no servidor. A animação é conforto, não regra.

   Arquivo próprio e `addEventListener` porque a CSP é `script-src 'self'`:
   não passa `<script>` com corpo nem `onload=` no HTML. */
(function () {
  var visor = document.querySelector("[data-crash-decorridos]");
  if (!visor) return;

  var numero = document.getElementById("crash-numero");
  if (!numero) return;

  var decorridosNoCarregamento = parseFloat(visor.dataset.crashDecorridos);
  var segundosParaDobrar = parseFloat(visor.dataset.crashDobrar);
  var alvo = parseFloat(visor.dataset.crashAlvo);
  if (!isFinite(decorridosNoCarregamento) || !(segundosParaDobrar > 0)) return;

  var referencia = Date.now();

  function desenhar() {
    var decorridos =
      decorridosNoCarregamento + (Date.now() - referencia) / 1000;
    var valor = Math.pow(2, decorridos / segundosParaDobrar);

    /* Passar do alvo é o servidor ter resolvido a rodada: a página está
       velha. Parar no alvo evita mostrar um número que já não vale — e
       recarregar traz o resultado de verdade. */
    if (isFinite(alvo) && valor >= alvo) {
      numero.textContent = alvo.toFixed(2);
      window.clearInterval(relogio);
      window.location.reload();
      return;
    }
    numero.textContent = valor.toFixed(2);
  }

  /* ~30 quadros por segundo: suave o bastante para parecer animação e leve o
     bastante para o celular da turma. */
  var relogio = window.setInterval(desenhar, 33);
  desenhar();
})();
