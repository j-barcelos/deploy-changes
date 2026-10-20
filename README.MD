# 🚀 Instalação e Inicialização Rápida do MySQL no Ubuntu

Guia prático para instalar o MySQL Server via APT e realizar o primeiro acesso de forma direta.

## 1. Instalação Direta
Atualize os repositórios do sistema e instale o MySQL Server:
```bash
sudo apt update && sudo apt install mysql-server -y
```

## 2. Iniciar e Habilitar o Serviço
Garanta que o banco de dados está rodando e configurado para iniciar junto com o sistema:
```bash
# Inicia o serviço do MySQL
sudo systemctl start mysql

# Habilita para iniciar no boot do Ubuntu
sudo systemctl enable mysql
```

## 3. Primeiro Acesso
No Ubuntu, o primeiro acesso do usuário `root` é feito nativamente utilizando os privilégios do sistema (`sudo`):
```bash
sudo mysql
```

## 4. Criar Senha e Liberar Acesso (Opcional)
Se você precisa definir uma senha para conectar o `root` em ferramentas externas (como VS Code, DBeaver ou Workbench), execute os comandos abaixo **dentro do console do MySQL**:

```sql
-- Define a senha para o usuário root
ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY 'SuaSenhaAqui';

-- Aplica as alterações e sai
FLUSH PRIVILEGES;
EXIT;
```

## 5. Próximos Acessos
Após definir a senha na etapa anterior, conecte-se ao banco exigindo a credencial:
```bash
mysql -u root -p
```
