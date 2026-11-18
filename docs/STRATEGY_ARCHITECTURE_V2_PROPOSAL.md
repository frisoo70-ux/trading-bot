# Proposition d'architecture V2 — stratégies et gestion du risque

**Statut : proposition de conception uniquement.** Ce document ne modifie aucun paramètre, aucune stratégie exécutable et aucun comportement LIVE/PAPER.

## Décision proposée

Ne pas ajouter de stratégie supplémentaire. Réorganiser les modes existants en deux familles d'entrées principales, avec un filtre de régime commun, un moteur de risque indépendant et un moteur d'exécution séparé.

- **Famille tendance :** fusionner les idées utiles des modes A/B autour d'une entrée breakout + retest, et conserver le mode D comme entrée pullback.
- **Famille retournement :** conserver le mode L comme setup de sweep/reclaim ; C et F restent des candidats contre-tendance, soumis à des régimes et à des preuves statistiques suffisantes.
- **Mode E :** isoler en stratégie expérimentale tant que son flux intrabar n'est pas mesuré correctement et validé hors échantillon.

Il s'agit d'une architecture cible à tester en PAPER, pas d'une recommandation de mise en production immédiate.

## 1. Constats vérifiés dans `tradings_bot.py`

### 1.1 Les stratégies sont évaluées dans une seule fonction

`buy_signal(df)` calcule les conditions des modes A, B, C, D, E, F et L, choisit ensuite un seul `signal_mode` avec une priorité fixe : L, A, B, C, D, E, F.

Conséquence : lorsqu'un même marché valide plusieurs configurations, la priorité de code choisit le mode avant une comparaison homogène de leur qualité. Les performances par mode peuvent donc être biaisées par cette priorité.

### 1.2 Les modes A et B partagent beaucoup de conditions

Les deux demandent notamment une clôture au-dessus de l'EMA20, un breakout, un OBV montant et une bougie verte. Le mode B ajoute un score de pattern et des seuils complémentaires. Il est préférable de les comparer comme variantes d'une même famille plutôt que comme deux stratégies totalement indépendantes.

### 1.3 Mode D est un candidat de pullback, mais son D1 est désactivé

Le code indique explicitement que D1 (prix entre les deux EMA) est désactivé. D2 exige une traversée précédente des deux EMA puis une bougie supplémentaire de confirmation. Cette logique mérite un test séparé par régime et par symbole.

### 1.4 Mode E intrabar mélange données confirmées et prix courant

`mode_e_intrabar_signal()` utilise le prix courant, mais lit l'ouverture et le volume de la dernière bougie du dataframe, qui est construite à partir des bougies clôturées. Le scanner n'évalue les symboles que sur nouvelle bougie confirmée ou rescannage forcé. La logique actuelle ne constitue donc pas une mesure fiable du volume et de l'impulsion de la bougie en formation.

**Proposition :** soit supprimer l'ambition intrabar et traiter E comme un signal de clôture, soit construire un flux intrabar explicite avec ouverture, volume cumulé, horodatage et fréquence de rafraîchissement cohérents. Ne pas mélanger les deux méthodes.

### 1.5 Mode L n'a pas la même validation de risque en PAPER et LIVE

Le contrôle `MODE_L_MIN_RR = 1.25` est présent dans `paper_open_position()`. Il n'est pas appliqué dans le chemin d'entrée LIVE. La gestion LIVE du Mode L surveille le prix courant puis ferme via la fonction d'ordre existante ; ce n'est pas équivalent à un stop conditionnel protégé côté exchange.

**Proposition :** calculer le risque/rendement net de frais et slippage dans un Risk Engine commun, puis exiger la validation avant toute exécution, quel que soit le mode. Tout changement LIVE doit faire l'objet d'une PR de code séparée, d'une revue explicite et d'une autorisation de déploiement distincte.

### 1.6 La gestion du risque et les stratégies sont trop couplées

Les signaux, le choix du mode, les filtres adaptatifs, les stops et les sorties sont répartis dans une grande base de code. Une séparation claire facilitera les tests unitaires et évitera qu'une amélioration d'une stratégie modifie involontairement une autre.

## 2. Architecture cible proposée

```text
Market Data
  ├─ Bougies clôturées 15m / 1h / 4h
  ├─ Snapshot prix / carnet / funding
  └─ Contrôle fraîcheur et cohérence
          |
          v
Market Regime Engine
  ├─ Tendance / range / volatilité
  ├─ Contexte BTC
  └─ Conditions de marché tradables
          |
          v
Strategy Engines (indépendants)
  ├─ Trend Breakout + Retest (A/B rationalisés)
  ├─ EMA Pullback (D)
  ├─ Liquidity Sweep + Reclaim (L)
  └─ Experimental / Countertrend (C/F/E)
          |
          v
Candidate Normalizer & Ranker
  ├─ Même format de signal
  ├─ Score comparable et explicable
  └─ Pas de priorité fixe entre modes
          |
          v
Adaptive Filters (une seule application)
          |
          v
Risk Engine
  ├─ Validation directionnelle du stop et de la cible
  ├─ R:R net après frais / slippage / funding estimé
  ├─ Taille calculée selon risque en USDC
  ├─ Exposition totale et corrélation
  └─ Limites journalières et kill switch
          |
          v
Execution Manager
  ├─ PAPER : simulateur réaliste et conservateur
  └─ LIVE : ordres idempotents + protection native exchange
          |
          v
Trade Journal & Evaluation
  ├─ Diagnostics entrée / sortie
  ├─ MAE / MFE / coûts réels
  └─ Walk-forward et comparaison OOS
```

## 3. Définition des familles de stratégies

### A. Trend Breakout + Retest

- Régime 4h/1h cohérent avec le sens de la position.
- Niveau de cassure calculé uniquement sur des données disponibles avant le signal.
- Attendre le retest du niveau ou une confirmation définie à l'avance.
- Éviter l'achat d'une bougie déjà trop étendue par rapport à l'ATR.
- A et B deviennent des variantes comparées, sans priorité arbitraire.

### B. EMA Pullback (D)

- Tendance de fond confirmée.
- Pullback vers une zone EMA prédéfinie.
- Confirmation de reprise et invalidation claire.
- Comparer D1 et D2 en PAPER séparément avant de retenir l'une des variantes.

### C. Liquidity Sweep + Reclaim (L)

- Niveaux calculés à partir de pivots confirmés, sans regarder dans le futur.
- Sweep puis réintégration mesurable.
- Confirmation de structure facultative mais testée séparément.
- Stop derrière l'extrême du sweep, cible sur une zone opposée réellement identifiée.
- Rejeter le signal si le R:R net après coûts est inférieur au seuil.
- Distinguer clairement les niveaux techniques estimés de la liquidité réelle du carnet.

### D. Contre-tendance et expérimental (C/F/E)

- C et F ne doivent pas concurrencer les setups de tendance dans tous les régimes.
- Les activer uniquement dans les contextes où leur avantage est démontré.
- E reste expérimental tant que son volume intrabar et son timing ne sont pas correctement reconstruits.
- Conserver les signaux rejetés dans le journal pour permettre une comparaison sans filtre, mais sans les exécuter en LIVE.

## 4. Contrat commun d'un signal

Chaque moteur devra retourner un objet normalisé contenant au minimum :

- `strategy_id`, `strategy_version`, `symbol`, `side`, `timeframe`.
- `signal_time`, `source_candle_time`, `data_freshness`.
- `entry_reference`, `invalidation_price`, `target_price`.
- `setup_score`, `regime`, `confirmation_flags`.
- `reason_codes` et diagnostics reproductibles.

Un signal incomplet, périmé ou dont les niveaux sont du mauvais côté doit être rejeté avant le Risk Engine.

## 5. Risk Engine — règles à rendre communes

1. Calculer la distance au stop selon le sens LONG/SHORT.
2. Calculer le R:R sur prix d'exécution estimé, et non sur le dernier prix affiché.
3. Déduire frais d'entrée/sortie, slippage et funding estimé lorsque pertinent.
4. Calculer la quantité depuis un budget de risque en USDC ; ne pas utiliser une taille fixe qui ignore la distance au stop.
5. Contrôler exposition brute, marge, positions corrélées et nombre de positions.
6. Bloquer les entrées si les données sont périmées, le stop est invalide, le risque dépasse les limites ou l'état exchange est incohérent.
7. Réconcilier périodiquement les positions et les ordres réellement ouverts avec l'état local.

Les seuils de risque (pourcentage d'equity, perte journalière maximale, limites par actif) doivent être proposés et approuvés avant implémentation. Aucun nouveau seuil LIVE n'est introduit par ce document.

## 6. Protection des positions LIVE

Avant toute nouvelle architecture de stratégie en LIVE, traiter séparément la protection d'exécution :

- Vérifier les possibilités et contraintes exactes des ordres conditionnels Hyperliquid.
- Placer et vérifier la protection côté exchange après confirmation du fill.
- Gérer les fills partiels, rejets, annulations, redémarrages et réconciliation.
- Refuser une nouvelle entrée si la protection requise ne peut pas être confirmée.
- Prévoir une procédure de fermeture d'urgence et un kill switch.
- Tester d'abord sur environnement simulé et avec scénarios de panne.

Ce chantier ne doit pas être confondu avec la simple simulation d'un stop dans PAPER.

## 7. Plan de migration en étapes

| Étape | Travail | Garde-fou |
|---|---|---|
| 0 | Tests de caractérisation du comportement actuel | Aucun changement de logique |
| 1 | Extraire les fonctions de stratégie derrière une interface commune | Résultats identiques à l'ancien scanner |
| 2 | Introduire le normaliseur et le classement des candidats en mode shadow | Aucun ordre supplémentaire |
| 3 | Corriger le modèle de données intrabar de E, test isolé | E reste désactivé en LIVE |
| 4 | Comparer A/B/D/L par walk-forward | PAPER uniquement |
| 5 | Introduire le Risk Engine commun | D'abord shadow/PAPER |
| 6 | Étudier l'exécution protégée native | PR séparée et validation humaine |
| 7 | Revue des statistiques hors échantillon | Pas de déploiement automatique |

## 8. Protocole de validation

- Tests unitaires de chaque setup avec jeux de bougies synthétiques : signal valide, faux signal, données manquantes, bougie incomplète.
- Tests anti-lookahead : aucune donnée future ne doit influencer un pivot, une zone ou un signal.
- Backtest événementiel avec frais, slippage, funding, latence et règles de fill.
- Walk-forward chronologique : paramètres choisis sur une fenêtre, testés sur la fenêtre suivante.
- Séparer les résultats par stratégie, symbole, sens, régime et période.
- Comparer au buy-and-hold et à une stratégie de référence simple.
- Mesurer expectancy nette, profit factor, drawdown, Sharpe/Sortino, nombre de trades et stabilité inter-périodes.
- Exiger un échantillon suffisamment large et plusieurs régimes ; 30 trades par segment est un minimum exploratoire, pas une preuve.
- Ne retenir une stratégie que si le résultat reste robuste hors échantillon et après coûts.

## 9. Hors périmètre de cette PR

- Aucun changement de `tradings_bot.py`.
- Aucun changement de `SYMBOLS`, `PAPER_SYMBOLS`, tailles, levier ou paramètres de stratégie.
- Aucun changement à V7, Adaptive Intelligence, Auto Learning ou Auto-Optimizer.
- Aucun ordre, aucune clé API et aucune modification de compte Hyperliquid.
- Aucun changement de `execution_mode`.
- Aucun merge dans `main` et aucun déploiement Bot-Hosting.

**Conclusion :** cette PR documente une architecture et une séquence de travail sûres. Elle ne prétend pas améliorer la rentabilité tant que les moteurs isolés n'ont pas été testés et évalués hors échantillon.
