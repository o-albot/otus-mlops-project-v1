#!/bin/bash

# Цвета для вывода
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YELLOW}Generating variables.json for Airflow...${NC}"

# Перейти в директорию infra для получения outputs
cd infra

# Получить значения из Terraform outputs
YC_ZONE=$(terraform output -raw yc_zone 2>/dev/null)
YC_FOLDER_ID=$(terraform output -raw yc_folder_id 2>/dev/null)
YC_SUBNET_ID=$(terraform output -raw subnet_id 2>/dev/null)
DP_SA_ID=$(terraform output -raw dp_service_account_id 2>/dev/null)
DP_SECURITY_GROUP_ID=$(terraform output -raw dp_security_group_id 2>/dev/null)

# Получить DP_SA_JSON (многострочный, нужно экранировать для JSON)
DP_SA_JSON_RAW=$(terraform output -json dp_service_account_json 2>/dev/null | jq -c .)

if [ -z "$DP_SA_JSON_RAW" ] || [ "$DP_SA_JSON_RAW" = "null" ]; then
    echo -e "${RED}Error: DP_SA_JSON is empty or null${NC}"
    exit 1
fi

# Экранируем для вставки в JSON строку (заменяем " на \", убираем переносы строк)
DP_SA_JSON_ESCAPED=$(echo "$DP_SA_JSON_RAW" | jq -c . | sed 's/"/\\"/g')

# Вернуться в корень
cd ..

# Получить SSH ключ (ищем id_rsa.pub или id_ed25519.pub)
if [ -f ~/.ssh/id_rsa.pub ]; then
    YC_SSH_PUBLIC_KEY=$(cat ~/.ssh/id_rsa.pub | tr -d '\n')
elif [ -f ~/.ssh/id_ed25519.pub ]; then
    YC_SSH_PUBLIC_KEY=$(cat ~/.ssh/id_ed25519.pub | tr -d '\n')
else
    YC_SSH_PUBLIC_KEY=""
    echo -e "${YELLOW}Warning: SSH public key not found${NC}"
fi

# Получить MLflow URL
MLFLOW_IP=$(kubectl get svc -n mlops mlflow -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null)
if [ -n "$MLFLOW_IP" ] && [ "$MLFLOW_IP" != "<pending>" ]; then
    MLFLOW_TRACKING_URI="http://${MLFLOW_IP}:5000"
else
    MLFLOW_TRACKING_URI="http://mlflow-service.mlops:5000"
fi

# Создать variables.json с DP_SA_JSON
cat > variables.json << EOF
{
  "YC_ZONE": "${YC_ZONE}",
  "YC_FOLDER_ID": "${YC_FOLDER_ID}",
  "YC_SUBNET_ID": "${YC_SUBNET_ID}",
  "YC_SSH_PUBLIC_KEY": "${YC_SSH_PUBLIC_KEY}",
  "DP_SA_ID": "${DP_SA_ID}",
  "DP_SA_AUTH_KEY_PUBLIC_KEY": "${YC_SSH_PUBLIC_KEY}",
  "DP_SECURITY_GROUP_ID": "${DP_SECURITY_GROUP_ID}",
  "MLFLOW_TRACKING_URI": "${MLFLOW_TRACKING_URI}",
  "DP_SA_JSON": "${DP_SA_JSON_ESCAPED}"
}
EOF

echo -e "${GREEN}variables.json created successfully!${NC}"
echo ""
echo -e "${YELLOW}File size: $(wc -c < variables.json) bytes${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "1. Open Airflow UI -> Admin -> Variables -> Import Variables"
echo "2. Select variables.json"
echo "3. Click Import"