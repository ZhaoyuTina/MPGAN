# MPGAN

Code for Kansal et. al., *Particle Cloud Generation with Message Passing Generative Adversarial Networks*, NeurIPS 2021 [`arXiv:2106.11535`](https://arxiv.org/abs/2106.11535).


## Overview

This repository contains PyTorch code for the message passing GAN (MPGAN) [model](mpgan/model.py), as well as scripts for [training](train.py) the models from scratch, [generating](gen_samples.py) and [plotting](save_outputs.py) the particle clouds. 
We include also [weights](final_models) of the fully trained models discussed in the paper. 

Additionally, we release the standalone [JetNet](https://github.com/rkansal47/JetNet) library, which provides a PyTorch Dataset class for our JetNet dataset, as well as  implementations of the evaluation metrics discussed in the paper.

## Dependencies

#### MPGAN Model

 - `torch >= 1.8.0`

#### Training, Plotting, Evaluation

 - `torch >= 1.8.0`
 - `jetnet >= 0.0.3`
 - `numpy >= 1.21.0`
 - `matplotlib`
 - `mplhep`

#### External Models

 - `torch`
 - `torch_geometric`


A Docker image containing all necessary libraries can be found [here](https://gitlab-registry.nautilus.optiputer.net/raghsthebest/mnist-graph-gan:latest) ([Dockerfile](Dockerfile)).


## Training

Start training with `python train.py --name test_model --jets g [args]`. 

By default, model parameters, figures of particle and jet features, and plots of the training losses and evaluation metrics over time will be saved every five epochs in an automatically created `outputs/[name]` directory.

Some notes:
 - Will run on a GPU by default if available. 
 - The default arguments correspond to the final model architecture and training configuration used in the paper. 
 - Run `python train.py --help` or look at [setup_training.py](setup_training.py) for a full list of arguments.


## Generation (Not finalized!)

Pre-trained models can be used for data generation using the [gen_samples.py](gen_samples.py) script. 
By default it generates samples for all the models listed in the [final_models](final_models) directory and saves them in numpy format as `final_models/[model name]/samples.npy`. 
Samples for your own pre-trained models can be generated by adding the training args and model weights in the `final_models` directory with the same format and running `gen_samples.py`.

