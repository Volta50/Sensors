/*
  ESP32 - Lectura de voltaje con ADC + muestreo por comando

  Pin ADC: GPIO34 (ADC1_CH6). Usa pines ADC1 (32-39) para evitar
  conflictos con WiFi. GPIO34-39 son solo entrada.

  Rango de entrada con atenuación 11 dB: ~0.1 V a ~3.1 V
  ¡NUNCA apliques más de 3.3 V al pin!

  Comandos (Monitor Serie a 115200, cualquier fin de línea):
    m  -> toma 100 muestras en 1 segundo y muestra resultados
    c  -> activa/desactiva la impresión continua del voltaje
    h  -> muestra la ayuda
*/

const int ADC_PIN = 34;

// --- Configuración del muestreo ---
const int           N_SAMPLES          = 100;                              // muestras por ráfaga
const unsigned long SAMPLE_WINDOW_MS   = 1000;                             // duración total (1 s)
const unsigned long SAMPLE_INTERVAL_US = SAMPLE_WINDOW_MS * 1000UL / N_SAMPLES; // 10 ms entre muestras

// --- Impresión continua ---
const unsigned long PRINT_INTERVAL_MS = 500;
bool continuous = true;
unsigned long lastPrint = 0;

uint32_t samplesMv[N_SAMPLES];

// Lee el voltaje en mV (usa la calibración de fábrica del ESP32)
uint32_t readMilliVolts() {
  return analogReadMilliVolts(ADC_PIN);
}

void printHelp() {
  Serial.println(F("\n=== Comandos ==="));
  Serial.println(F("m : tomar 100 muestras en 1 s"));
  Serial.println(F("c : activar/desactivar lectura continua"));
  Serial.println(F("h : ayuda\n"));
}

void takeSamples() {
  Serial.println(F("\n>> Muestreando (100 muestras / 1 s)..."));

  unsigned long nextTime = micros();
  unsigned long t0 = micros();

  for (int i = 0; i < N_SAMPLES; i++) {
    // Espera activa hasta el instante exacto de la siguiente muestra
    while ((long)(micros() - nextTime) < 0) { }
    samplesMv[i] = readMilliVolts();
    nextTime += SAMPLE_INTERVAL_US;
  }

  unsigned long elapsedMs = (micros() - t0) / 1000UL;

  // --- Estadísticas ---
  uint32_t minV = samplesMv[0], maxV = samplesMv[0];
  double sum = 0;
  for (int i = 0; i < N_SAMPLES; i++) {
    if (samplesMv[i] < minV) minV = samplesMv[i];
    if (samplesMv[i] > maxV) maxV = samplesMv[i];
    sum += samplesMv[i];
  }
  double mean = sum / N_SAMPLES;

  double var = 0;
  for (int i = 0; i < N_SAMPLES; i++) {
    double d = samplesMv[i] - mean;
    var += d * d;
  }
  double stdev = sqrt(var / N_SAMPLES);

  // --- Resultados ---
  Serial.println(F("--- Muestras (indice, mV) ---"));
  for (int i = 0; i < N_SAMPLES; i++) {
    Serial.print(i);
    Serial.print(',');
    Serial.println(samplesMv[i]);
  }

  Serial.println(F("--- Resumen ---"));
  Serial.printf("Muestras : %d\n", N_SAMPLES);
  Serial.printf("Duracion : %lu ms\n", elapsedMs);
  Serial.printf("Promedio : %.3f V\n", mean / 1000.0);
  Serial.printf("Minimo   : %.3f V\n", minV / 1000.0);
  Serial.printf("Maximo   : %.3f V\n", maxV / 1000.0);
  Serial.printf("Desv.est.: %.4f V\n\n", stdev / 1000.0);
}

void setup() {
  Serial.begin(115200);
  delay(500);

  analogReadResolution(12);                        // 0-4095
  analogSetPinAttenuation(ADC_PIN, ADC_11db);      // rango ~0-3.1 V

  Serial.println(F("ESP32 ADC listo."));
  printHelp();
}

void loop() {
  // --- Comandos por serial ---
  while (Serial.available()) {
    char cmd = Serial.read();
    if (cmd == '\n' || cmd == '\r' || cmd == ' ') continue;

    switch (cmd) {
      case 'm': case 'M':
        takeSamples();
        lastPrint = millis();
        break;
      case 'c': case 'C':
        continuous = !continuous;
        Serial.println(continuous ? F("Lectura continua: ON") : F("Lectura continua: OFF"));
        break;
      case 'h': case 'H':
        printHelp();
        break;
      default:
        Serial.print(F("Comando desconocido: "));
        Serial.println(cmd);
        break;
    }
  }

  // --- Lectura continua del voltaje ---
  if (continuous && millis() - lastPrint >= PRINT_INTERVAL_MS) {
    lastPrint = millis();
    uint32_t mv = readMilliVolts();
    Serial.printf("Voltaje: %.3f V  (%u mV)\n", mv / 1000.0, mv);
  }
}
