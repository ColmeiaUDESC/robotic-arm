// Braço robótico (MasterJain/RoboticArm) — controle pelo PC via cabo USB
// Os servos usam os mesmos pinos do projeto original: D8 a D13.
//
// Comandos (texto, um por linha, 115200 baud):
//   S i a      -> move o servo i (1 a 6) para o ângulo a (0 a 180)
//   M a1 .. a6 -> move os 6 servos de uma vez; responde "DONE" quando chegam
//   H          -> volta para a posição inicial; responde "DONE" quando chega
//   V n        -> velocidade: n milissegundos por grau (1 = rápido, 40 = lento)
//   ?          -> responde "POS a1 a2 a3 a4 a5 a6" com a posição atual

#include <Servo.h>

const uint8_t N = 6;
const uint8_t PINOS[N]   = {8, 9, 10, 11, 12, 13};

// Posição ao ligar (a mesma de INICIAL no controle_braco.py)
//                         base ombro cotov punho giro garra
const uint8_t INICIAL[N] = {53,  23,   59,   114,  0,   10};

// Limites de cada servo. Se algum bater na estrutura, aperte o limite aqui.
// (mesmos valores de LIMITES no controle_braco.py)
//                         base ombro cotov punho giro garra
const uint8_t MINIMO[N]  = {0,   0,    0,    0,    0,   10};
const uint8_t MAXIMO[N]  = {180, 180,  180,  180,  180, 110};  // garra: 10 = fechada, 110 = aberta

// Faixa de pulso da biblioteca Servo (igual ao servo.write normal)
const int US_MIN = 544, US_MAX = 2400;

Servo servos[N];
int16_t posAtual[N];   // em DÉCIMOS de grau (900 = 90,0°) para o movimento sair liso
int16_t alvo[N];       // idem
uint16_t msPorGrau = 15;
unsigned long ultimaAtualizacao = 0;
uint16_t sobra = 0;    // tempo que sobrou da última conta, para não perder passo
bool avisarQuandoChegar = false;

char linha[48];
uint8_t tam = 0;

int16_t limita(uint8_t i, int a) {
  return constrain(a, MINIMO[i], MAXIMO[i]) * 10;
}

void escrever(uint8_t i) {
  // décimos de grau -> microssegundos: passos bem menores que 1 grau
  servos[i].writeMicroseconds(US_MIN + (long)(US_MAX - US_MIN) * posAtual[i] / 1800);
}

void setup() {
  Serial.begin(115200);
  for (uint8_t i = 0; i < N; i++) {
    posAtual[i] = alvo[i] = limita(i, INICIAL[i]);
    escrever(i);                   // define a posição antes de ligar o sinal, evita tranco
    servos[i].attach(PINOS[i], US_MIN, US_MAX);
    delay(250);                    // liga um servo por vez para não puxar corrente de uma vez
  }
  ultimaAtualizacao = millis();
  Serial.println(F("PRONTO"));
}

void tratarComando(char *c) {
  int i, a, v[N];
  switch (c[0]) {
    case 'S':
      if (sscanf(c + 1, "%d %d", &i, &a) == 2 && i >= 1 && i <= N) {
        alvo[i - 1] = limita(i - 1, a);
      }
      break;
    case 'M':
      if (sscanf(c + 1, "%d %d %d %d %d %d", &v[0], &v[1], &v[2], &v[3], &v[4], &v[5]) == N) {
        for (uint8_t k = 0; k < N; k++) alvo[k] = limita(k, v[k]);
        avisarQuandoChegar = true;
      }
      break;
    case 'H':
      for (uint8_t k = 0; k < N; k++) alvo[k] = limita(k, INICIAL[k]);
      avisarQuandoChegar = true;
      break;
    case 'V':
      if (sscanf(c + 1, "%d", &a) == 1) msPorGrau = constrain(a, 1, 40);
      break;
    case '?':
      Serial.print(F("POS"));
      for (uint8_t k = 0; k < N; k++) { Serial.print(' '); Serial.print((posAtual[k] + 5) / 10); }
      Serial.println();
      break;
  }
}

void lerSerial() {
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') {
      if (tam > 0) { linha[tam] = '\0'; tratarComando(linha); tam = 0; }
    } else if (tam < sizeof(linha) - 1) {
      linha[tam++] = ch;
    }
  }
}

void mover() {
  unsigned long agora = millis();
  unsigned long dt = agora - ultimaAtualizacao;
  if (dt < 2) return;                       // atualiza no máximo a cada 2 ms
  ultimaAtualizacao = agora;

  // quantos décimos de grau andar neste intervalo (10 décimos por msPorGrau)
  uint32_t total = dt * 10UL + sobra;
  uint16_t passo = total / msPorGrau;
  sobra = total % msPorGrau;
  if (passo == 0) return;

  bool todosChegaram = true;
  for (uint8_t i = 0; i < N; i++) {
    int16_t dif = alvo[i] - posAtual[i];
    if (dif == 0) continue;
    if (dif > 0) posAtual[i] += min((int16_t)passo, dif);
    else         posAtual[i] -= min((int16_t)passo, (int16_t)-dif);
    escrever(i);
    if (posAtual[i] != alvo[i]) todosChegaram = false;
  }
  if (avisarQuandoChegar && todosChegaram) {
    Serial.println(F("DONE"));
    avisarQuandoChegar = false;
  }
}

void loop() {
  lerSerial();
  mover();
}
