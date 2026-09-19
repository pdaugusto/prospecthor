import os
import shutil
import sys

src_dir = r"C:\Users\PEDRO\.gemini\antigravity\scratch\prospector-bot"
dest_dir = r"D:\Prospecthor"

# Exclude list
exclude_dirs = {"venv", "__pycache__", ".pytest_cache", ".vercel"}
exclude_files = {"bot.log"}

def copy_project():
    print(f"Iniciando cópia de:\n  {src_dir}\npara:\n  {dest_dir}\n")
    
    if not os.path.exists(src_dir):
        print(f"Erro: O diretório de origem {src_dir} não existe.")
        return
        
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)
        print(f"Diretório de destino {dest_dir} criado.")
        
    copied_files_count = 0
    copied_dirs_count = 0
    
    for root, dirs, files in os.walk(src_dir):
        # Filtra os diretórios excluídos
        # Modificar a lista in-place para que o os.walk não entre neles
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        
        # Calcula o caminho correspondente no destino
        rel_path = os.path.relpath(root, src_dir)
        if rel_path == ".":
            dest_root = dest_dir
        else:
            dest_root = os.path.join(dest_dir, rel_path)
            
        if not os.path.exists(dest_root):
            os.makedirs(dest_root)
            copied_dirs_count += 1
            
        for file in files:
            if file in exclude_files:
                continue
            src_file = os.path.join(root, file)
            dest_file = os.path.join(dest_root, file)
            
            try:
                shutil.copy2(src_file, dest_file)
                copied_files_count += 1
            except Exception as e:
                print(f"Erro ao copiar {src_file}: {e}")
                
    print(f"\nCópia concluída com sucesso!")
    print(f"Diretórios criados: {copied_dirs_count}")
    print(f"Arquivos copiados: {copied_files_count}")

if __name__ == "__main__":
    copy_project()
