# Guia de Contribuição — AquaH₂ AI-SCADA

Obrigado pelo interesse em contribuir! Este guia explica como participar do desenvolvimento.

## Configuração do ambiente de desenvolvimento

```bash
git clone https://github.com/SEU-USUARIO/aquah2-scada.git
cd aquah2-scada
python3 run.py   # zero dependências — funciona imediatamente
```

Para o backend modular (FastAPI):
```bash
cd backend
pip install -r requirements.txt
python3 main.py
```

## Executar os testes antes de qualquer contribuição

```bash
cd backend
python3 test_system.py
# Esperado: Results: 21/21 passed | 0 failed
```

## Fluxo de contribuição

1. **Fork** o repositório
2. **Crie uma branch** com nome descritivo:
   - `feat/nome-da-funcionalidade` — para novas funcionalidades
   - `fix/descricao-do-bug` — para correções
   - `docs/o-que-foi-documentado` — para documentação
3. **Faça seus commits** seguindo o padrão Conventional Commits:
   ```
   feat: adicionar suporte a protocolo DNP3
   fix: corrigir cálculo de eficiência quando stack desligado
   docs: documentar endpoints da API WebSocket
   test: adicionar teste de integração para ESD
   ```
4. **Push** para sua branch
5. **Abra um Pull Request** descrevendo as mudanças

## O que pode ser contribuído

- Novos protocolos de hardware (DNP3, EtherNet/IP, BACnet, CANbus)
- Algoritmos de IA adicionais (LSTM, Random Forest para anomalias)
- Novas telas na interface (ex: relatórios de produção diária)
- Suporte a novos tipos de equipamentos (SOEC, AEM, alcalino)
- Traduções (inglês, espanhol)
- Testes automatizados
- Melhorias de documentação

## Padrão de código

- Python: PEP 8, comentários em português ou inglês
- JavaScript: ES6+, sem frameworks obrigatórios
- Commits: Conventional Commits (feat/fix/docs/test/refactor)
- Sem dependências externas no `run.py` (self-contained é um requisito de design)

## Dúvidas?

Abra uma [Issue](https://github.com/SEU-USUARIO/aquah2-scada/issues) com a label `question`.
