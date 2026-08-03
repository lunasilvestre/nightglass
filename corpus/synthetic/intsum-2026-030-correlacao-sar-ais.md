---
doc_id: intsum-2026-030-correlacao-sar-ais
title: Metodologia de Correlação SAR–AIS
classification: UNCLASSIFIED // SYNTHETIC
origin: synthetic
publisher: NIGHTGLASS synthetic corpus
doc_type: intsum
language: pt
date: 2026-04-02
aoi: [lisbon, kattegat]
serial: INTSUM 2026/030
licence: Documento sintético escrito para este projeto. Reutilização livre.
redistributable: true
---

UNCLASSIFIED // SYNTHETIC

*Documento sintético. Escrito para exercitar um sistema de recuperação de
informação. Não descreve nenhuma embarcação, operador ou evento real.*

# INTSUM 2026/030 — Metodologia de Correlação SAR–AIS

## 1. Sequência

A correlação executa-se sempre pela mesma ordem, e cada passo deixa registo:

1. **Seleção da cena.** Pesquisa no catálogo por área de interesse e janela
   temporal. Retêm-se apenas produtos GRD em modo IW com polarização dupla.
2. **Deteção.** Executa-se sobre a polarização **VH**. A retrodifusão do mar em
   VH é mais baixa do que em VV, pelo que o contraste navio–fundo é superior e o
   limiar de deteção é mais estável.
3. **Filtro de comprimento.** Descartam-se candidatos abaixo do comprimento
   mínimo configurado (por omissão 15 m), para reduzir falsos positivos de
   origem oceanográfica.
4. **Preparação do AIS.** Recorte à envolvente da cena e à janela de aquisição,
   seguido de eliminação de duplicados.
5. **Emparelhamento.** Cada deteção é confrontada com as posições AIS
   interpoladas ao instante exato de aquisição.
6. **Classificação.** `matched` ou `dark`, sempre com a distância e o desvio
   temporal registados.

## 2. Eliminação de duplicados — obrigatória, e antes do emparelhamento

As redes terrestres de AIS retransmitem a mesma mensagem a partir de várias
estações. Numa janela de aquisição típica, a fração de linhas que são cópias
exatas é elevada — na ordem dos setenta por cento — e chegaram a observar-se
mais de vinte cópias idênticas de uma única mensagem.

A chave de deduplicação é **(MMSI, timestamp, latitude, longitude)**. Se a
eliminação não for feita antes do emparelhamento, qualquer lógica de
"mais próximo no tempo" fica enviesada pelo número de retransmissões, que nada
tem a ver com a embarcação.

## 3. Interpolação em vez de vizinho mais próximo

Após deduplicação, uma embarcação típica apresenta várias dezenas de instantes
distintos ao longo de uma janela de poucos minutos. Isso é suficiente para
**interpolar a trajetória ao instante exato de aquisição**, em vez de aceitar a
posição bruta mais próxima no tempo. A diferença é material: a 12 nós, um erro
de dois minutos corresponde a cerca de 740 m — mais do que o raio de
emparelhamento habitual, ou seja, suficiente para transformar uma embarcação
declarada numa falsa deteção escura.

## 4. Tolerância assimétrica

Ver INTREP 2026/041. O deslocamento em azimute desloca alvos em movimento ao
longo da direção de voo, e não radialmente. Um raio simétrico é a forma mais
comum de fabricar deteções escuras a partir de tráfego perfeitamente normal.

## 5. Registo

Cada emparelhamento transporta a fonte AIS utilizada e um indicador de se essa
fonte é ou não verdade de referência nesta área. Sem esse indicador, uma taxa de
embarcações escuras não é citável.

Fiabilidade da fonte **B**, credibilidade da informação **2**.
