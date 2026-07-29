# Build de Produção Windows

Execute em uma máquina Windows limpa, com Python 3.11+ e Inno Setup 6.

```powershell
py -m venv .venv-build
.\.venv-build\Scripts\python -m pip install --upgrade pip
.\.venv-build\Scripts\python -m pip install -r requirements.txt -r requirements-build.txt
.\.venv-build\Scripts\pyinstaller --clean --noconfirm GeradorImagensProduto.spec
```

Saída onedir esperada:

```text
dist\GeradorImagensProduto\GeradorImagensProduto.exe
```

Valide o executável em uma máquina Windows sem Python instalado. O banco,
logs e configurações são gravados em:

```text
%APPDATA%\GeradorImagensProduto
```

Depois da validação:

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\GeradorImagensProduto.iss
```

Saída esperada:

```text
release\GeradorImagensProduto-Setup-0.1.0.exe
```
