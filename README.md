# AquaH₂ AI-SCADA Platform

<div align="center">

![AquaH2 Banner](docs/banner.png)

**Plataforma industrial de controle e automação por IA para produção de Hidrogênio Verde + Dessalinização SWRO**

[![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-00C9A7?style=flat-square)](https://github.com/kraefegg/aquah2-scada)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![IEC 62443](https://img.shields.io/badge/IEC%2062443-compliant-0D2B4E?style=flat-square)](docs/security.md)
[![IEC 61511](https://img.shields.io/badge/IEC%2061511-SIL--2-red?style=flat-square)](docs/safety.md)

[🚀 Início Rápido](#-início-rápido) · [📸 Screenshots](#-screenshots) · [📡 API](#-api-rest) · [🔌 Hardware Real](#-conectar-hardware-real) · [🤖 IA](#-motor-de-ia)

</div>

---

## ✨ O que é

O **AquaH₂ AI-SCADA** é uma plataforma SCADA industrial completa — desenvolvida pela **Kraefegg M.O.** com o Developer **Railson** — para supervisão, controle e automação por inteligência artificial de uma planta de **hidrogênio verde** (eletrolisador PEM 50 MW) integrada com **dessalinização de água do mar** (SWRO 5.000 m³/dia) no Nordeste do Brasil.

O sistema roda com **`python3 run.py`** — sem pip, sem instalação, sem dependências externas.

---

## 🖥️ Screenshots

| Dashboard | Diagrama de Processo |
|-----------|---------------------|
| ![Dashboard](docs/screen_dashboard.png) | ![PID](docs/screen_pfd.png) |

| Eletrolisador PEM | IA Operacional |
|-------------------|----------------|
| ![Electrolyzer](docs/screen_electrolyzer.png) | ![AI](docs/screen_ai.png) |

| Segurança ESD | Rede IoT |
|---------------|----------|
| ![Safety](docs/screen_safety.png) | ![Network](docs/screen_network.png) |

---

## 🚀 Início Rápido

**Único requisito:** Python 3.8+

```bash
# 1. Clone o repositório
git clone https://github.com/SEU-USUARIO/aquah2-scada.git
cd aquah2-scada

# 2. Execute
python3 run.py

# 3. Abra no browser
# http://localhost:8765
```

**Login padrão:** `railson.kraefegg` / `AquaH2@2026`

> O browser abre automaticamente após 1,5 segundos.

---

## 📦 Estrutura do Projeto

```
aquah2-scada/
│
├── run.py                      # 🔑 Servidor completo (arquivo único, ~1.400 linhas)
├── aquah2_platform.html        # 🖥️  Interface SCADA (10 telas, Chart.js, SVG P&ID)
│
├── docs/
│   ├── architecture.md         # Arquitetura do sistema
│   ├── api.md                  # Referência completa da API
│   ├── hardware.md             # Guia de conexão de hardware real
│   ├── security.md             # Conformidade IEC 62443
│   └── safety.md               # Sistema ESD e IEC 61511 SIL-2
│
├── backend/                    # Módulos independentes (opcional — run.py é auto-suficiente)
│   ├── simulator.py            # Simulador físico da planta
│   ├── ai_engine.py            # Motor de IA (PID, anomalias, preditivo, chat)
│   ├── database.py             # Camada SQLite (série temporal)
│   ├── config.py               # Limites de engenharia e configurações
│   └── main.py                 # Servidor FastAPI (requer pip install)
│
├── tests/
│   └── test_system.py          # 21 testes unitários e de integração
│
├── .github/
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug_report.md
│   │   └── feature_request.md
│   └── workflows/
│       └── test.yml            # GitHub Actions — testes automáticos
│
├── requirements.txt            # Dependências (apenas para o backend FastAPI)
├── LICENSE                     # MIT License
└── README.md                   # Este arquivo
```

---

## 🏭 Funcionalidades

### 10 Telas de Operação

| Tela | Descrição |
|------|-----------|
| **Dashboard** | KPIs em tempo real, gráficos Chart.js, rings de eficiência animados |
| **Diagrama de Processo** | P&ID SVG animado com fluxos ao vivo, todos os equipamentos clicáveis |
| **Eletrolisador PEM** | Stacks A e B — 8 parâmetros cada, controle PID, tabelas de limites |
| **Dessalinização SWRO** | 9 parâmetros de qualidade, fouling por vaso, alerta preditivo CIP |
| **Gestão de Energia** | Solar PV + Eólica + BESS, curva de potência eólica física |
| **Armazenamento H₂/NH₃** | Tanques com visualização de nível, síntese Haber-Bosch |
| **Alarmes & Eventos** | IEC 62682, confirmação individual, histórico com timestamps |
| **Rede IoT** | Topologia híbrida (WiFi6/Ethernet), status de 39 nós |
| **Segurança & ESD** | Detectores H₂ LEL, NH₃ ppm, PSVs, ESD IEC 61511 SIL-2 |
| **IA Operacional** | Chat contextual em português, decisões autônomas, métricas |

### Motor de IA Embarcado

- **PID controllers** para temperatura dos stacks (ajuste autônomo de fluxo H₂O)
- **Z-score** em janela deslizante de 30 amostras para detecção de anomalias
- **Regressão linear** (slope) para tendências e manutenção preditiva
- **ESD automático** em limites críticos (temperatura, pressão, H₂ LEL, NH₃)
- **Otimização energética** — balanço automático Solar/Eólica/BESS
- **Chat contextual** com dados reais da planta (embutido ou via API Claude)

### Banco de Dados SQLite (zero instalação)

```
sensors       → série temporal de todos os sensores (2s interval, 72h retention)
events        → log completo de alarmes, decisões IA e ocorrências
setpoints     → histórico de alterações com operador e fonte
chat          → histórico de conversas com o assistente
```

---

## 📡 API REST

O servidor expõe API REST completa na porta `8765`:

```bash
# Estado completo da planta
GET  http://localhost:8765/api/state

# Histórico de um sensor (últimas 24h)
GET  http://localhost:8765/api/history/stack_a_temp

# Alterar setpoint
POST http://localhost:8765/api/setpoint
     {"tag": "stack_b_flow", "value": 21.5}

# Chat com IA
POST http://localhost:8765/api/chat
     {"message": "status da planta"}

# Parada de emergência
POST http://localhost:8765/api/esd

# Confirmar alarme
POST http://localhost:8765/api/alarms/ack
     {"code": "ALM-0004", "operator": "railson"}
```

**WebSocket** bidirecional em `ws://localhost:8765/ws` para dados em tempo real.

[📖 Referência completa da API →](docs/api.md)

---

## 🔌 Conectar Hardware Real

O método `Plant.tick()` em `run.py` é o único ponto de integração. Em modo simulado, calcula a física da planta. Em produção, substitua pelo driver do seu hardware:

### MODBUS TCP (Siemens S7, Allen-Bradley, ABB)

```python
# pip install pymodbus
from pymodbus.client import ModbusTcpClient

client = ModbusTcpClient('192.168.1.100', port=502)
client.connect()

# No método tick():
regs = client.read_holding_registers(address=0, count=20, slave=1)
self._state['stack_a']['temp']     = regs.registers[0] / 10.0
self._state['stack_a']['pressure'] = regs.registers[1] / 10.0
self._state['stack_a']['h2_nm3h']  = regs.registers[3] / 10.0
```

### OPC-UA (Siemens TIA Portal, Beckhoff TwinCAT)

```python
# pip install opcua
from opcua import Client

client = Client("opc.tcp://192.168.1.101:4840")
client.connect()
node = client.get_node("ns=2;i=1001")
self._state['stack_a']['temp'] = node.get_value()
```

### MQTT (ESP32, Raspberry Pi, LoRaWAN)

```python
# pip install paho-mqtt
import paho.mqtt.client as mqtt

def on_message(client, userdata, msg):
    data = json.loads(msg.payload)
    if msg.topic == "aquah2/stack_a/temp":
        plant._state['stack_a']['temp'] = float(data['value'])

mqtt_client = mqtt.Client()
mqtt_client.connect("localhost", 1883)
mqtt_client.subscribe("aquah2/#")
mqtt_client.loop_start()
```

[📖 Guia completo de integração de hardware →](docs/hardware.md)

---

## 🤖 Motor de IA

### Integração com Claude (Anthropic)

Para respostas do assistente com acesso completo ao contexto da planta via LLM:

```python
# No topo de run.py:
ANTHROPIC_API_KEY = "sk-ant-..."

# Ou via variável de ambiente:
export ANTHROPIC_API_KEY=sk-ant-...
python3 run.py
```

Sem a chave, o sistema usa respostas contextuais embutidas que já cobrem os casos de uso operacionais mais comuns.

---

## 🛡️ Segurança

| Norma | Aplicação |
|-------|-----------|
| **IEC 62443-3-3** | Segurança de sistemas de controle industrial |
| **IEC 61511 SIL-2** | Sistema instrumentado de segurança (ESD) |
| **ISO 50001** | Gestão de energia |
| **IEC 62682** | Gerenciamento de alarmes industriais |

Para deploy em produção industrial, consulte [docs/security.md](docs/security.md).

---

## 🧪 Testes

```bash
cd backend
python3 test_system.py
```

```
Results: 21/21 passed | 0 failed
```

Testes cobrem: simulador físico, motor de IA, banco de dados, PID, rolling stats, integração completa.

---

## 🗺️ Roadmap

- [ ] Autenticação JWT e controle de perfis de acesso
- [ ] Export de relatórios PDF automatizado
- [ ] Dashboard mobile responsivo
- [ ] Connector Grafana / InfluxDB (via adaptador)
- [ ] Módulo de previsão de geração renovável (LSTM)
- [ ] Suporte a múltiplas plantas (multi-tenant)
- [ ] App mobile React Native para monitoramento remoto

---

## 🤝 Contribuição

Contribuições são bem-vindas! Por favor:

1. Fork o repositório
2. Crie uma branch: `git checkout -b feature/nova-funcionalidade`
3. Commit: `git commit -m 'feat: descrição da mudança'`
4. Push: `git push origin feature/nova-funcionalidade`
5. Abra um Pull Request

Consulte [CONTRIBUTING.md](CONTRIBUTING.md) para detalhes.

---

## 📄 Licença

MIT License — veja [LICENSE](LICENSE) para detalhes.

---

## 👤 Autor

**Kraefegg M.O. — Engineering Development & Project Finance**

Developer: **Railson** — Lead Developer

> Projeto AquaH₂ Hub · Nordeste, Brasil · KRG-2026-AQH2-001-SCADA

---

<div align="center">

⭐ Se este projeto foi útil, considere dar uma estrela!

[![GitHub stars](https://img.shields.io/github/stars/SEU-USUARIO/aquah2-scada?style=social)](https://github.com/SEU-USUARIO/aquah2-scada)

</div>
