import torch
import torch.optim as optim
from quicktorch.utils import train, imshow
from quicktorch.data import mnist, cifar
from igcn import IGCN
import math


def main():
    dsets = ['mnist']  # , 'cifar']
    names = ['5', '7', '9']  # '3', 
    no_gabors = [2, 4, 8, 16, 32]
    max_gabor = [False]  # , False]
    no_epochs = 300
    rot_pools = [True]
    accs = []
    epochs = []
    models = []
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")


    for dset in dsets:
        for model_name in names:
            for no_g in no_gabors:
                for rot_pool in rot_pools:
                    for max_g in max_gabor:
                        print("Training igcn{} on {} with rot_pool={}, no_g={}, max_g={}".format(model_name, dset, rot_pool, no_g, max_g))
                        if dset == 'mnist':
                            train_loader, test_loader, _ = mnist(batch_size=int(4096 // no_g), rotate=True, num_workers=8)
                        if dset == 'cifar':
                            train_loader, test_loader, _ = cifar(batch_size=2048)

                        model = IGCN(no_g=no_g, model_name=model_name, dset=dset, rot_pool=rot_pool, max_gabor=max_g).to(device)

                        total_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
                        print("Total parameter size: " + str(total_params*32/1000000) + "M")

                        optimizer = optim.Adam(model.parameters(), lr=1e-4)
                        a, e = train(model, [train_loader, test_loader], save=False,
                                    epochs=no_epochs, opt=optimizer, device=device)
                        accs.append(a)
                        epochs.append(e)
                        models.append(dset+"_"+model_name)
                        f = open("results.txt", "a+")
                        f.write("\n" + dset + "\t" + model_name + "\t\t" + str(no_g) + "\t\t" + str(rot_pool) + "\t" + str(max_g) + "\t" + str(a) + "\t" + str(e) + "\t\t" + str(no_epochs))
                        f.close()
                        del(model)
                        torch.cuda.empty_cache()

    print(accs, epochs, models)



if __name__ == "__main__":
    main()