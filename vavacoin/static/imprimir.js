/* Imprimir o comprovante.

   No Benbals o botão era `onclick="window.print()"` no HTML. A CSP daqui é
   `script-src 'self'` e não aceita handler inline, então a mesma chamada
   mora num arquivo servido pelo próprio site.

   É o único jeito de "salvar" o comprovante: o diálogo de impressão do
   navegador exporta em PDF, e no celular vira imagem para mandar na
   conversa. Nada é gerado no servidor — o `@media print` do base.css tira o
   casco da página e o que sobra é a folha do recibo. */
(function () {
  document.querySelectorAll("button.imprimir").forEach(function (botao) {
    botao.addEventListener("click", function () {
      window.print();
    });
  });
})();
