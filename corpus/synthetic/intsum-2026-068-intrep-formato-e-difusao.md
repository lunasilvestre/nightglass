---
doc_id: intsum-2026-068-intrep-formato-e-difusao
title: Formato e Difusão de INTREP — Marcações e Portão de Revisão
classification: UNCLASSIFIED // SYNTHETIC
origin: synthetic
publisher: NIGHTGLASS synthetic corpus
doc_type: intsum
language: pt
date: 2026-07-02
aoi: [lisbon, kattegat]
serial: INTSUM 2026/068
licence: Documento sintético escrito para este projeto. Reutilização livre.
redistributable: true
---

UNCLASSIFIED // SYNTHETIC

*Documento sintético. Escrito para exercitar um sistema de recuperação de
informação. Não descreve nenhuma embarcação, operador ou evento real.*

# INTSUM 2026/068 — Formato e Difusão de INTREP

## 1. Estrutura mínima

Um INTREP produzido nesta célula contém, por esta ordem:

1. **Marcação de classificação**, na primeira linha e na última.
2. **Serial e data-hora** de produção, em UTC.
3. **Área de interesse** e janela temporal a que o relatório se refere.
4. **Sumário** — não mais do que três frases.
5. **Observações**, cada uma com as suas referências.
6. **Avaliação**, com graduação de fiabilidade e credibilidade.
7. **Ressalvas**, incluindo obrigatoriamente a ressalva permanente sobre
   deteções escuras.
8. **Fontes**, enumeradas.

## 2. Referências obrigatórias por afirmação

Cada afirmação factual transporta as referências que a suportam: identificador
da cena, identificadores das deteções, identificadores dos excertos documentais
citados, e as marcas temporais aplicáveis.

Uma afirmação sem referências **não é publicada com ressalva — é removida**. A
distinção importa: uma afirmação com fraca sustentação pode ser graduada como
tal; uma afirmação sem proveniência não é graduável de todo.

## 3. Propagação de marcações

A marcação de classificação do relatório é determinada pela **marcação mais
restritiva** de qualquer fonte citada. A marcação não é escolhida pelo redator e
não pode ser reduzida por reformulação.

Em concreto: se um excerto citado tiver marcação `UNCLASSIFIED // SYNTHETIC`,
essa marcação acompanha o produto e tem de ficar visível nele. Um relatório que
cite material sintético e se apresente como `UNCLASSIFIED` simples está
incorretamente marcado, ainda que todo o seu conteúdo esteja certo.

## 4. Portão de revisão humana

Todo o produto gerado nasce com a marcação **`DRAFT — NOT RELEASABLE`**. A
marcação só é removida por decisão humana explícita, registada, e após leitura
integral do rascunho.

O portão não é uma formalidade administrativa. É o ponto em que se verifica que
as referências existem, que as ressalvas estão presentes e que a marcação
propagada está correta — três coisas que um gerador automático pode omitir de
forma perfeitamente fluente.

## 5. Linguagem

Preferir **"pode não ser declarada"** a "não está declarada". Preferir
**"consistente com"** a "demonstra". Evitar quantificadores sem denominador
("vários", "numerosos") — indicar o número e o total.

## 6. Fiabilidade

Fiabilidade da fonte **B**, credibilidade da informação **2**.
