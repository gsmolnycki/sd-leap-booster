from diffusers import StableDiffusionPipeline
from transformers import CLIPFeatureExtractor, CLIPTextModel, CLIPTokenizer
import torch
import gc
import os
import nltk
import unicodedata
import re
import argparse
from nltk.corpus import stopwords
from tqdm.rich import tqdm
from iteration_utilities import grouper

file_path = os.path.abspath(os.path.dirname(__file__))

def parse_args(args=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--pretrained_model_name_or_path", type=str, default="stabilityai/stable-diffusion-2-1-base")
    parser.add_argument("--images_per_prompt", type=int, default=2)
    parser.add_argument("--batch_size", type=int, default=8)
    parser.add_argument("--use_cpu", type=bool, action=argparse.BooleanOptionalAction)
    parser.add_argument("--output_folder", type=str, default=os.path.join(file_path, "sd_extracted"))
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--height", type=int, default=512)
    return parser.parse_args(args)

def slugify(value):
    """
    Converts to lowercase, removes non-word characters (alphanumerics and
    underscores) and converts spaces to hyphens. Also strips leading and
    trailing whitespace.
    """
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    value = re.sub('[^\w\s-]', '', value).strip().lower()
    return re.sub('[-\s]+', '-', value)

imagenet_templates_small = [
    "{}, DLSR photo",
    "{}, 3D render",
    "{}, pencil drawing",
    "{}, watercolor painting",
    "{}, oil painting",
    "{}, anime",
    "{}, cartoon",
    "{}, comic book",
    "{}, line art",
    "{}, vector art",
    "{}, clip art",
    "{}, sculpture",
    "{}, digital painting"
]

def main():
    args = parse_args()
    nltk.download('stopwords')
    model_id_or_path = args.pretrained_model_name_or_path
    if args.use_cpu:
        pipeline = StableDiffusionPipeline.from_pretrained(
            model_id_or_path,
            torch_dtype=torch.float32,
        )
        pipeline = pipeline.to("cpu")
    else:
        pipeline = StableDiffusionPipeline.from_pretrained(
            model_id_or_path,
            revision="fp16",
            torch_dtype=torch.float16,
        )
        pipeline = pipeline.to("cuda")
    tokenizer = CLIPTokenizer.from_pretrained(
        model_id_or_path,
        subfolder="tokenizer",
    )
    text_encoder = CLIPTextModel.from_pretrained(
        model_id_or_path, subfolder="text_encoder"
    )
    token_embeds = text_encoder.get_input_embeddings().weight.data
    pipeline.set_progress_bar_config(disable=True)
    stopwords_english = stopwords.words('english')
    tokens_to_search = []

    common_english_words = {}
    with open(os.path.join(file_path, "bip39.txt"), 'r') as f:
        lines = f.readlines()
        for line in lines:
            if len(line) > 0:
                common_english_words[line.strip()] = True

    for token_id in range(token_embeds.shape[0]):
        token_name = tokenizer.decode(token_id)
        token_id = tokenizer.encode(token_name, add_special_tokens=False)
        if len(token_id) > 1:
            continue

        if len(token_name) > 3 and token_name.isalnum() and not token_name in stopwords_english and token_name in common_english_words:
            tokens_to_search.append(token_name)
    
    tokens_to_search = sorted(list(set(tokens_to_search)))

    for token_idx, token_name in enumerate(tqdm(tokens_to_search, desc="Extracting tokens")):
        token_id = tokenizer.encode(token_name, add_special_tokens=False)
        if len(token_id) > 1:
            raise Exception("Need single token!")
        token_type = "train"
        if len(tokens_to_search) - token_idx <= 4:
            token_type = "val"
        image_output_folder = os.path.join(args.output_folder, token_type, token_name)
        if os.path.exists(image_output_folder):
            tqdm.write(f"Skipping {image_output_folder} because it already exists")
            continue
        learned_embeds = token_embeds[token_id][0]
        concept_images_folder = os.path.join(image_output_folder, 'concept_images')
        os.makedirs(concept_images_folder, exist_ok = True)
        learned_embeds_dict = {token_name: learned_embeds.detach().cpu()}
        torch.save(learned_embeds_dict, os.path.join(image_output_folder, "learned_embeds.bin"))
        
        prompts = []
        for text in imagenet_templates_small:
            prompts.append(text.format(token_name))
        prompts *= args.images_per_prompt
        
        for promptGroup in grouper(prompts, args.batch_size):
            tqdm.write("Inferring prompts: {}".format(" ".join(["\"" + str(prompt) + "\"," for prompt in promptGroup])[:-1].strip()))
            images = pipeline(list(promptGroup), num_inference_steps=50, guidance_scale=9, width=args.width, height=args.height).images
            for n, image in enumerate(zip(images, promptGroup)):
                image[0].save(os.path.join(concept_images_folder, "image_{}_{}.png".format(slugify(image[1]), n)))


if __name__ == "__main__":
    main()

