/*
  ESP32 - Muestreo de voltaje con ADC para calibración voltaje -> nivel (cm)

  Pin ADC: GPIO34 (ADC1_CH6). Usa pines ADC1 (32-39) para evitar
  conflictos con WiFi. GPIO34-39 son solo entrada.

  Rango de entrada con atenuación 11 dB: ~0.1 V a ~3.1 V
  ¡NUNCA apliques más de 3.3 V al pin!

  Comandos (Monitor Serie / script Python a 115200, terminados en \n):
    m,<nivel_cm>  -> toma 100 muestras en 1 s y las envía crudas. Ej: m,1  m,2.5
    c             -> activa/desactiva la impresión continua del voltaje
    h             -> muestra la ayuda

  Formato de respuesta a m,<nivel>  (lo interpreta calibracion.py):
    BEGIN,<nivel>
    S,<indice>,<mV>      (x100)
    END,<n_muestras>,<duracion_ms>

  Las estadísticas (media, desviación estándar, etc.) las calcula el script Python.
*/

const int ADC_PIN = 34;

// --- Configuración del muestreo ---
const int           N_SAMPLES          = 100;                                   // muestras por ráfaga
const unsigned long SAMPLE_WINDOW_MS   = 1000;                                  // duración total (1 s)
const unsigned long SAMPLE_INTERVAL_US = SAMPLE_WINDOW_MS * 1000UL / N_SAMPLES; // 10 ms entre muestras

// --- Impresión continua (desactivada por defecto para no ensuciar el protocolo) ---
const unsigned long PRINT_INTERVAL_MS = 500;
bool continuous = false;
unsigned long lastPrint = 0;

uint32_t samplesMv[N_SAMPLES];

// --- Buffer de línea para comandos ---
char rxBuf[32];
uint8_t rxLen = 0;

// Lee el voltaje en mV (usa la calibración de fábrica del ESP32)
uint32_t readMilliVolts() {
  return analogReadMilliVolts(ADC_PIN);
}

void printHelp() {
  Serial.println(F("\n=== Comandos ==="));
  Serial.println(F("m,<nivel_cm> : tomar 100 muestras en 1 s (ej: m,1  m,2.5)"));
  Serial.println(F("c            : activar/desactivar lectura continua"));
  Serial.println(F("h            : ayuda\n"));
}

void takeSamples(const char* levelStr) {
  unsigned long t0 = micros();
  unsigned long nextTime = t0;

  // Se muestrea primero y se imprime después, para que el Serial no afecte el timing
  for (int i = 0; i < N_SAMPLES; i++) {
    // Espera activa hasta el instante exacto de la siguiente muestra
    while ((long)(micros() - nextTime) < 0) { }
    samplesMv[i] = readMilliVolts();
    nextTime += SAMPLE_INTERVAL_US;
  }

  unsigned long elapsedMs = (micros() - t0) / 1000UL;

  Serial.printf("BEGIN,%s\n", levelStr);
  for (int i = 0; i < N_SAMPLES; i++) {
    Serial.printf("S,%d,%lu\n", i, (unsigned long)samplesMv[i]);
  }
  Serial.printf("END,%d,%lu\n", N_SAMPLES, elapsedMs);
}

void handleCommand(char* line) {
  // Quitar espacios finales
  int len = strlen(line);
  while (len > 0 && line[len - 1] == ' ') line[--len] = '\0';
  if (len == 0) return;

  switch (line[0]) {
    case 'm': case 'M': {
      char* p = line + 1;
      while (*p == ',' || *p == ' ') p++;     // acepta "m,1" y "m 1"
      char* endp;
      strtof(p, &endp);                       // solo para validar que sea un número
      if (endp == p) {
        Serial.println(F("ERR,uso: m,<nivel_cm>  (ej: m,1)"));
      } else {
        takeSamples(p);
        lastPrint = millis();
      }
      break;
    }
    case 'c': case 'C':
      continuous = !continuous;
      Serial.println(continuous ? F("Lectura continua: ON") : F("Lectura continua: OFF"));
      break;
    case 'h': case 'H':
      printHelp();
      break;
    default:
      Serial.print(F("ERR,comando desconocido: "));
      Serial.println(line);
      break;
  }
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
  // --- Comandos por serial (se acumulan hasta fin de línea) ---
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') {
      if (rxLen > 0) {
        rxBuf[rxLen] = '\0';
        handleCommand(rxBuf);
        rxLen = 0;
      }
    } else if (rxLen < sizeof(rxBuf) - 1) {
      rxBuf[rxLen++] = ch;
    }
  }

  // --- Lectura continua del voltaje ---
  if (continuous && millis() - lastPrint >= PRINT_INTERVAL_MS) {
    lastPrint = millis();
    uint32_t mv = readMilliVolts();
    Serial.printf("Voltaje: %.3f V  (%lu mV)\n", mv / 1000.0, (unsigned long)mv);
  }
}
