# Récupère un jeton Keycloak et le place dans le presse-papiers (Windows).
#
#   .\scripts\get_token.ps1 -Username hosni -Password <mot de passe>
#
# Flux "Direct access grants" : pour le développement uniquement. En
# production, un front-end utilise Authorization Code avec PKCE.

param(
    [string]$Username = "hosni",
    [Parameter(Mandatory = $true)][string]$Password,
    [string]$Realm = "monrealm",
    [string]$ClientId = "financial-agent-client",
    [string]$KeycloakUrl = "http://localhost:8080"
)

$response = curl.exe -s -X POST "$KeycloakUrl/realms/$Realm/protocol/openid-connect/token" `
    -d "grant_type=password" `
    -d "client_id=$ClientId" `
    -d "username=$Username" `
    -d "password=$Password" | ConvertFrom-Json

if (-not $response.access_token) {
    Write-Host "Echec de l'obtention du jeton :" -ForegroundColor Red
    $response | ConvertTo-Json
    exit 1
}

$response.access_token | Set-Clipboard
Write-Host "Jeton copie dans le presse-papiers (valide $($response.expires_in)s)." -ForegroundColor Green
Write-Host "Collez-le dans Swagger via le bouton Authorize, SANS le mot 'Bearer'."
