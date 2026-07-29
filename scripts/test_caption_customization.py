import os
import sys

# Adiciona o diretório raiz ao path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.pipeline import generate_captions

def test_caption_formatting():
    print("🧪 Running subtitle customization format tests...")
    
    # Mock parameters
    W, H = 1080, 1920
    workdir = "."
    
    # Nós mockamos transcribe_whisper para não bater na API da OpenAI
    import lib.pipeline
    original_transcribe = lib.pipeline.transcribe_whisper
    lib.pipeline.transcribe_whisper = lambda *args, **kwargs: {
        "text": "teste de legenda personalizada",
        "words": [
            {"word": "teste", "start": 0.0, "end": 1.0},
            {"word": "de", "start": 1.0, "end": 2.0},
            {"word": "legenda", "start": 2.0, "end": 3.0},
            {"word": "personalizada", "start": 3.0, "end": 4.0}
        ]
    }
    
    try:
        # Caso 1: Testar cor Hex normal (ex: #FF0000 -> Vermelho) e posição "meio"
        ass_path = generate_captions(
            video_path="dummy.mp4",
            openai_key="dummy_key",
            W=W,
            H=H,
            workdir=workdir,
            caption_color="#FF0000",
            caption_position="meio"
        )
        
        with open(ass_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        # Verificar Vermelho (Hex #FF0000 -> ASS ABGR: &H000000FF)
        assert "&H000000FF" in content, f"Erro: Vermelho não encontrado na legenda. Conteúdo:\n{content}"
        # Verificar posição Meio (50% de H 1920 -> MarginV: 960)
        assert ",960,1" in content, f"Erro: Margem vertical para meio (960) incorreta. Conteúdo:\n{content}"
        print("✅ Teste 1: Vermelho (#FF0000) e Posição Meio funcionou!")
        
        # Limpar arquivo gerado
        if os.path.exists(ass_path):
            os.remove(ass_path)
            
        # Caso 2: Testar cor Hex de 3 dígitos (ex: #0F0 -> Verde) e posição "abaixo_do_meio"
        ass_path = generate_captions(
            video_path="dummy.mp4",
            openai_key="dummy_key",
            W=W,
            H=H,
            workdir=workdir,
            caption_color="0F0",
            caption_position="below_middle"
        )
        
        with open(ass_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        # Verificar Verde (Hex 0F0 -> #00FF00 -> ASS ABGR: &H0000FF00)
        assert "&H0000FF00" in content, f"Erro: Verde não encontrado na legenda. Conteúdo:\n{content}"
        # Verificar posição Abaixo do Meio (30% de H 1920 -> MarginV: 576)
        assert ",576,1" in content, f"Erro: Margem vertical para abaixo do meio (576) incorreta. Conteúdo:\n{content}"
        print("✅ Teste 2: Verde (0F0) e Posição Abaixo do Meio funcionou!")
        
        # Limpar arquivo gerado
        if os.path.exists(ass_path):
            os.remove(ass_path)
            
        # Caso 3: Valores padrão (None)
        ass_path = generate_captions(
            video_path="dummy.mp4",
            openai_key="dummy_key",
            W=W,
            H=H,
            workdir=workdir
        )
        
        with open(ass_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        # Verificar Branco Padrão (&H00FFFFFF)
        assert "&H00FFFFFF" in content, f"Erro: Branco padrão não encontrado. Conteúdo:\n{content}"
        # Verificar posição Baixo Padrão (15% de H 1920 -> MarginV: 288)
        assert ",288,1" in content, f"Erro: Margem vertical padrão (288) incorreta. Conteúdo:\n{content}"
        print("✅ Teste 3: Branco padrão e Posição Baixo padrão funcionou!")
        
        if os.path.exists(ass_path):
            os.remove(ass_path)
            
        print("\n🎉 ALL TESTS PASSED SUCCESSFULLY!")
        
    finally:
        # Restaurar original
        lib.pipeline.transcribe_whisper = original_transcribe

if __name__ == "__main__":
    test_caption_formatting()
